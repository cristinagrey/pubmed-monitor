#!/usr/bin/env python3
"""
PubMed 文献自动检索与邮件推送工具
根据关键词自动检索 PubMed 并推送相关文献到邮箱
"""

import os
import json
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.parse import urlencode, quote
from urllib.error import URLError
import xml.etree.ElementTree as ET
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('pubmed_monitor.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 期刊影响因子参考表 (2024年近似值)
JOURNAL_IF = {
    'nature': 64.8, 'science': 56.9, 'cell': 64.5,
    'the new england journal of medicine': 158.5, 'lancet': 168.9,
    'jama': 120.7, 'nature medicine': 82.9, 'nature reviews drug discovery': 120.1,
    'nature reviews microbiology': 76.2, 'nature reviews immunology': 100.3,
    'nature communications': 16.6, 'cell host & microbe': 30.3,
    'cell reports': 9.4, 'pnas': 11.1, 'journal of virology': 5.4,
    'virology': 3.7, 'antiviral research': 10.1, 'journal of antimicrobial chemotherapy': 5.2,
    'plos pathogens': 7.4, 'plos one': 3.7, 'molecular cell': 14.5,
    'embo journal': 11.4, 'nucleic acids research': 14.9,
    'journal of biological chemistry': 4.0, 'virus research': 6.3,
    'viruses': 5.8, 'frontiers in microbiology': 5.2, 'frontiers in immunology': 7.3,
    'scientific reports': 4.6, 'acs infectious diseases': 7.3,
    'journal of medicinal chemistry': 7.3, 'retrovirology': 3.0,
    'emerging infectious diseases': 11.8, 'hepatology': 13.9,
}

KEYWORD_CATEGORIES = {
    'Ebola/丝状病毒': ['ebola', 'filovirus', 'marburg', 'vp40', 'vp24', 'nucleoprotein', 'gp glycoprotein'],
    '流感病毒': ['influenza', 'flu', 'neuraminidase', 'hemagglutinin'],
    '冠状病毒/COVID-19': ['coronavirus', 'sars-cov', 'covid', 'mers'],
    'HIV/逆转录病毒': ['hiv', 'retrovir', 'reverse transcriptase'],
    '肝炎病毒': ['hepatitis', 'hbv', 'hcv'],
    '广谱抗病毒': ['broad-spectrum antiviral', 'antiviral agent', 'antiviral drug', 'viral inhibitor'],
}


def get_journal_if(journal_name):
    if not journal_name:
        return 0.0
    name_lower = journal_name.lower().strip()
    if name_lower in JOURNAL_IF:
        return JOURNAL_IF[name_lower]
    for key, value in JOURNAL_IF.items():
        if key in name_lower or name_lower in key:
            return value
    return 1.0


def classify_article(article, keywords):
    title_lower = article.get('title', '').lower()
    abstract_lower = article.get('abstract', '').lower()
    article_keywords_lower = [kw.lower() for kw in article.get('keywords', [])]
    combined_text = title_lower + ' ' + abstract_lower + ' ' + ' '.join(article_keywords_lower)
    
    for category, category_keywords in KEYWORD_CATEGORIES.items():
        for ck in category_keywords:
            if ck in combined_text:
                return category
    
    for keyword in keywords:
        kw_lower = keyword.lower()
        if any(w in kw_lower for w in ['ebola', 'filovirus', 'vp40', 'nucleoprotein']):
            return 'Ebola/丝状病毒'
        elif any(w in kw_lower for w in ['influenza', 'flu']):
            return '流感病毒'
        elif any(w in kw_lower for w in ['coronavirus', 'sars', 'covid', 'mers']):
            return '冠状病毒/COVID-19'
        elif any(w in kw_lower for w in ['hiv', 'retrovirus', 'reverse transcriptase']):
            return 'HIV/逆转录病毒'
        elif any(w in kw_lower for w in ['hepatitis', 'hbv', 'hcv']):
            return '肝炎病毒'
    
    return '其他相关研究'


def translate_with_niutrans(text):
    url = "http://api.niutrans.com/NiuTransServer/translation"
    params = {
        'from': 'en', 'to': 'zh',
        'apikey': 'fba2b7e7737a1eb72bb0a7dee04e04e6',
        'src_text': text[:5000]
    }
    req = Request(f"{url}?{urlencode(params)}", headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(req, timeout=20) as response:
        data = json.loads(response.read().decode('utf-8'))
    return data.get('tgt_text')


def translate_with_google(text):
    url = "https://translate.googleapis.com/translate_a/single"
    params = {'client': 'gtx', 'sl': 'en', 'tl': 'zh-CN', 'dt': 't', 'q': text[:5000]}
    req = Request(f"{url}?{urlencode(params)}", headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(req, timeout=20) as response:
        data = json.loads(response.read().decode('utf-8'))
    translated = ''.join([s[0] for s in data[0] if s[0]])
    return translated if translated else None


def translate_to_chinese(text, max_retries=2):
    if not text or len(text.strip()) == 0:
        return ""
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    if chinese_chars > len(text) * 0.3:
        return text
    
    translators = [translate_with_niutrans, translate_with_google]
    for attempt in range(max_retries):
        for translator in translators:
            try:
                result = translator(text)
                if result and len(result) > 0:
                    return result
            except Exception as e:
                logger.debug(f"翻译失败: {e}")
                time.sleep(0.5)
    return text


def batch_translate(texts, max_workers=5):
    if not texts:
        return []
    results = [''] * len(texts)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {executor.submit(translate_to_chinese, text): idx for idx, text in enumerate(texts)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception:
                results[idx] = texts[idx]
    return results


class PubMedMonitor:
    def __init__(self, config_file='config.json'):
        self.config = self.load_config(config_file)
        self.seen_pmids = self.load_seen_pmids()
        
    def load_config(self, config_file):
        default_config = {
            "keywords": ["Ebola virus"],
            "email_settings": {
                "sender_email": "", "sender_password": "",
                "receiver_email": "", "smtp_server": "smtp.gmail.com", "smtp_port": 587
            },
            "search_settings": {
                "max_results": 20, "days_back": 10,
                "email_subject_prefix": "PubMed文献推荐",
                "articles_per_email": 20, "max_emails": 1
            }
        }
        
        if os.path.exists(config_file):
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                for key in default_config:
                    if key not in config:
                        config[key] = default_config[key]
                    elif isinstance(default_config[key], dict):
                        for sub_key in default_config[key]:
                            if sub_key not in config[key]:
                                config[key][sub_key] = default_config[key][sub_key]
        else:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)
            config = default_config
        
        # 从环境变量覆盖邮箱配置（GitHub Actions 使用）
        if os.environ.get('SENDER_EMAIL'):
            config['email_settings']['sender_email'] = os.environ['SENDER_EMAIL']
        if os.environ.get('SENDER_PASSWORD'):
            config['email_settings']['sender_password'] = os.environ['SENDER_PASSWORD']
        if os.environ.get('RECEIVER_EMAIL'):
            config['email_settings']['receiver_email'] = os.environ['RECEIVER_EMAIL']
        
        return config
    
    def load_seen_pmids(self, seen_file='seen_pmids.json'):
        if os.path.exists(seen_file):
            with open(seen_file, 'r') as f:
                data = json.load(f)
                return data[-2000:] if len(data) > 2000 else data
        return []
    
    def save_seen_pmids(self, seen_file='seen_pmids.json'):
        with open(seen_file, 'w') as f:
            json.dump(self.seen_pmids[-2000:], f)
    
    def search_pubmed(self, keyword, max_results=20, days_back=30):
        base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        date_range = f"{start_date:%Y/%m/%d}:{end_date:%Y/%m/%d}[edat]"
        pub_type = "(Journal Article[pt] OR Review[pt])"
        query = f"{keyword} AND {date_range} AND {pub_type}"
        
        search_params = {'db': 'pubmed', 'term': query, 'retmax': max_results, 'retmode': 'json', 'sort': 'relevance'}
        search_url = f"{base_url}esearch.fcgi?{urlencode(search_params)}"
        
        try:
            logger.info(f"正在搜索: {keyword}")
            with urlopen(search_url, timeout=30) as response:
                search_data = json.loads(response.read().decode('utf-8'))
            pmids = search_data.get('esearchresult', {}).get('idlist', [])
            logger.info(f"找到 {len(pmids)} 篇文献")
            return pmids
        except Exception as e:
            logger.error(f"搜索出错: {e}")
            return []
    
    def fetch_article_details(self, pmids):
        if not pmids:
            return []
        base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
        articles = []
        
        for i in range(0, len(pmids), 20):
            batch = pmids[i:i+20]
            fetch_params = {'db': 'pubmed', 'id': ','.join(batch), 'retmode': 'xml'}
            fetch_url = f"{base_url}efetch.fcgi?{urlencode(fetch_params)}"
            try:
                with urlopen(fetch_url, timeout=60) as response:
                    xml_data = response.read().decode('utf-8')
                root = ET.fromstring(xml_data)
                for article in root.findall('.//PubmedArticle'):
                    article_info = self.parse_article(article)
                    if article_info:
                        article_info['title_cn'] = ''
                        articles.append(article_info)
                time.sleep(0.3)
            except Exception as e:
                logger.error(f"获取文章详情出错: {e}")
        return articles
    
    def parse_article(self, article_elem):
        try:
            medline = article_elem.find('.//MedlineCitation')
            article = medline.find('.//Article')
            pmid = medline.find('.//PMID').text if medline.find('.//PMID') is not None else ''
            title_elem = article.find('.//ArticleTitle')
            title = ''.join(title_elem.itertext()) if title_elem is not None else ''
            
            abstract_parts = []
            for abs_text in article.findall('.//AbstractText'):
                label = abs_text.get('Label', '')
                text = ''.join(abs_text.itertext()) or ''
                abstract_parts.append(f"{label}: {text}" if label else text)
            full_abstract = ' '.join(abstract_parts)
            abstract = full_abstract[:500] + '...' if len(full_abstract) > 500 else full_abstract
            
            authors = []
            for author in article.findall('.//Author'):
                last_name = author.find('LastName')
                first_name = author.find('ForeName')
                if last_name is not None:
                    name = last_name.text + (f" {first_name.text}" if first_name is not None else '')
                    authors.append(name)
            
            journal = article.find('.//Journal/Title')
            journal_name = journal.text if journal is not None else 'Unknown'
            
            pub_date = article.find('.//PubDate')
            date_str = ''
            if pub_date is not None:
                year = pub_date.find('Year')
                month = pub_date.find('Month')
                if year is not None:
                    date_str = year.text + (f" {month.text}" if month is not None else '')
            
            doi = ''
            for id_elem in article_elem.findall('.//ArticleId'):
                if id_elem.get('IdType') == 'doi':
                    doi = id_elem.text
                    break
            
            keywords = [kw.text for kw in medline.findall('.//Keyword') if kw.text]
            
            return {
                'pmid': pmid, 'title': title, 'title_cn': '',
                'authors': authors[:5], 'journal': journal_name, 'date': date_str,
                'abstract': abstract, 'doi': doi, 'keywords': keywords[:5],
                'url': f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                'impact_factor': get_journal_if(journal_name)
            }
        except Exception as e:
            logger.error(f"解析文章出错: {e}")
            return None
    
    def format_email_content(self, articles, category, batch_num=1, total_batches=1):
        if not articles:
            return None
        
        subject = f"{self.config['search_settings']['email_subject_prefix']}: {category} ({batch_num}/{total_batches}, {len(articles)}篇)"
        
        articles_html = ""
        for i, article in enumerate(articles, 1):
            authors_str = ', '.join(article['authors']) + (' et al.' if len(article['authors']) >= 5 else '')
            keywords_html = ''.join([f'<span class="keyword">{kw}</span>' for kw in article['keywords']])
            
            if_val = article.get('impact_factor', 0)
            if_class = 'if-high' if if_val >= 10 else ('if-medium' if if_val >= 3 else 'if-low')
            if_text = f'IF: {if_val:.1f}' if if_val > 0 else 'IF: 未知'
            
            articles_html += f"""
            <div class="article">
                <div class="article-header">
                    <div style="flex:1;"><div class="title">{i}. {article['title']}</div>
                    <div class="title-cn">{article.get('title_cn', '')}</div></div>
                    <span class="if-badge {if_class}">{if_text}</span>
                </div>
                <div class="authors">{authors_str}</div>
                <div class="journal">{article['journal']} | {article['date']}</div>
                <div class="abstract">{article['abstract']}</div>
                <div class="keywords">{keywords_html}</div>
                <div class="link"><a href="{article['url']}" target="_blank">查看原文</a>
                {f' | DOI: <a href="https://doi.org/{article["doi"]}" target="_blank">{article["doi"]}</a>' if article['doi'] else ''}</div>
            </div>"""
        
        html_content = f"""<html><head><style>
        body {{ font-family: Arial, 'Microsoft YaHei', sans-serif; line-height: 1.6; color: #333; background: #f5f5f5; }}
        .container {{ max-width: 800px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 25px; text-align: center; border-radius: 10px 10px 0 0; }}
        .stats {{ background: white; padding: 15px; text-align: center; border-bottom: 1px solid #eee; }}
        .article {{ background: white; margin: 15px; padding: 20px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); border-left: 4px solid #667eea; }}
        .article-header {{ display: flex; justify-content: space-between; align-items: flex-start; }}
        .title {{ font-size: 16px; font-weight: bold; color: #1a73e8; margin-bottom: 8px; }}
        .title-cn {{ font-size: 14px; color: #d32f2f; font-style: italic; background: #fff3e0; padding: 5px 10px; border-radius: 4px; border-left: 3px solid #ff9800; }}
        .if-badge {{ background: #ff9800; color: white; padding: 3px 10px; border-radius: 15px; font-size: 12px; font-weight: bold; white-space: nowrap; margin-left: 10px; }}
        .if-high {{ background: #4caf50; }} .if-medium {{ background: #ff9800; }} .if-low {{ background: #9e9e9e; }}
        .authors {{ color: #666; font-size: 14px; }} .journal {{ color: #888; font-style: italic; font-size: 13px; }}
        .abstract {{ margin-top: 12px; font-size: 13px; color: #555; padding: 10px; background: #f9f9f9; border-radius: 5px; }}
        .keyword {{ background: linear-gradient(135deg, #e8f5e9, #c8e6c9); padding: 3px 10px; border-radius: 15px; margin: 2px; font-size: 11px; display: inline-block; }}
        .link {{ margin-top: 12px; }} .link a {{ color: #667eea; text-decoration: none; font-weight: bold; }}
        .footer {{ text-align: center; margin-top: 20px; padding: 15px; color: #888; font-size: 12px; background: white; border-radius: 0 0 10px 10px; }}
        </style></head><body><div class="container">
        <div class="header"><h1>PubMed 文献推荐</h1><p>{category}</p><p style="font-size:12px;">{datetime.now().strftime('%Y-%m-%d %H:%M')}</p></div>
        <div class="stats"><span>共 {len(articles)} 篇文献</span><span>按影响因子排序</span></div>
        {articles_html}
        <div class="footer"><p>PubMed 文献监控工具</p></div>
        </div></body></html>"""
        
        return subject, html_content
    
    def send_email(self, subject, html_content):
        email_settings = self.config['email_settings']
        if not email_settings['sender_email'] or not email_settings['receiver_email']:
            logger.error("邮件配置不完整")
            return False
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = email_settings['sender_email']
            msg['To'] = email_settings['receiver_email']
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            port = email_settings['smtp_port']
            with (smtplib.SMTP_SSL(email_settings['smtp_server'], port, timeout=30) if port == 465 
                  else smtplib.SMTP(email_settings['smtp_server'], port, timeout=30)) as server:
                if port != 465:
                    server.starttls()
                server.login(email_settings['sender_email'], email_settings['sender_password'])
                server.send_message(msg)
            
            logger.info(f"邮件已发送: {subject}")
            return True
        except Exception as e:
            logger.error(f"发送邮件出错: {e}")
            return False
    
    def run(self):
        logger.info("=" * 50)
        logger.info("开始 PubMed 文献监控任务")
        logger.info("=" * 50)
        
        all_new_articles = []
        articles_per_email = self.config['search_settings'].get('articles_per_email', 20)
        max_emails = self.config['search_settings'].get('max_emails', 1)
        
        logger.info("第一步：检索文献...")
        for keyword in self.config['keywords']:
            pmids = self.search_pubmed(keyword, self.config['search_settings']['max_results'], self.config['search_settings']['days_back'])
            new_pmids = [p for p in pmids if p not in self.seen_pmids]
            if new_pmids:
                logger.info(f"'{keyword}' 发现 {len(new_pmids)} 篇新文献")
                articles = self.fetch_article_details(new_pmids)
                new_articles = [a for a in articles if a and a['pmid'] not in self.seen_pmids]
                all_new_articles.extend(new_articles)
                for article in new_articles:
                    self.seen_pmids.append(article['pmid'])
            else:
                logger.info(f"'{keyword}' 没有新文献")
        
        seen = set()
        unique_articles = [a for a in all_new_articles if a['pmid'] not in seen and not seen.add(a['pmid'])]
        all_new_articles = unique_articles
        logger.info(f"共收集 {len(all_new_articles)} 篇去重后的新文献")
        
        if not all_new_articles:
            self.save_seen_pmids()
            return []
        
        logger.info(f"第二步：并行翻译 {len(all_new_articles)} 篇标题...")
        titles = [a['title'] for a in all_new_articles]
        translated_titles = batch_translate(titles, max_workers=8)
        for article, title_cn in zip(all_new_articles, translated_titles):
            article['title_cn'] = title_cn
        logger.info("翻译完成")
        
        before_count = len(all_new_articles)
        all_new_articles = [a for a in all_new_articles if a.get('impact_factor', 0) > 1]
        filtered = before_count - len(all_new_articles)
        if filtered > 0:
            logger.info(f"过滤 {filtered} 篇 IF<=1 文献，剩余 {len(all_new_articles)} 篇")
        
        if not all_new_articles:
            self.save_seen_pmids()
            return []
        
        categorized = {}
        for article in all_new_articles:
            category = classify_article(article, self.config['keywords'])
            categorized.setdefault(category, []).append(article)
        
        for category in categorized:
            categorized[category].sort(key=lambda x: x.get('impact_factor', 0), reverse=True)
        
        emails_sent = 0
        for category, articles in categorized.items():
            if emails_sent >= max_emails:
                break
            total_batches = (len(articles) + articles_per_email - 1) // articles_per_email
            for batch_num in range(total_batches):
                if emails_sent >= max_emails:
                    break
                batch = articles[batch_num * articles_per_email:(batch_num + 1) * articles_per_email]
                result = self.format_email_content(batch, category, batch_num + 1, total_batches)
                if result and self.send_email(*result):
                    emails_sent += 1
                    time.sleep(1)
        
        self.save_seen_pmids()
        logger.info(f"任务完成: {len(all_new_articles)} 篇文献, 发送 {emails_sent} 封邮件")
        return all_new_articles


def main():
    print("=" * 60)
    print("  PubMed 文献自动检索与邮件推送工具")
    print("=" * 60)
    monitor = PubMedMonitor()
    articles = monitor.run()
    print(f"\n处理完成: {len(articles)} 篇文献" if articles else "\n本次没有新文献")


if __name__ == '__main__':
    main()
