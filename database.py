import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), 'tracker.db')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database and creates the visitors table if it doesn't exist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS visitor_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            referrer TEXT,
            user_agent TEXT,
            full_url TEXT,
            is_naver_ad INTEGER DEFAULT 0,
            naver_keyword TEXT,
            naver_media TEXT,
            naver_ad_group TEXT,
            naver_ad TEXT,
            device_type TEXT
        )
    ''')
    conn.commit()
    conn.close()

def log_visitor(ip_address, referrer, user_agent, full_url, is_naver_ad, naver_keyword=None, naver_media=None, naver_ad_group=None, naver_ad=None, device_type='Unknown'):
    """Logs a new visitor entry into the database with local KST time."""
    # Convert UTC time to KST (UTC + 9)
    # Using datetime.utcnow() + timedelta(hours=9) to represent local South Korean time
    kst_now = datetime.utcnow() + timedelta(hours=9)
    timestamp_str = kst_now.strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO visitor_logs (
            timestamp, ip_address, referrer, user_agent, full_url, 
            is_naver_ad, naver_keyword, naver_media, naver_ad_group, naver_ad, device_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        timestamp_str, ip_address, referrer, user_agent, full_url,
        1 if is_naver_ad else 0, naver_keyword, naver_media, naver_ad_group, naver_ad, device_type
    ))
    conn.commit()
    conn.close()

def get_recent_logs(limit=100):
    """Retrieves recent visitor logs."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM visitor_logs
        ORDER BY id DESC
        LIMIT ?
    ''', (limit,))
    logs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return logs

def get_stats_summary():
    """Calculates statistics for the dashboard dashboard."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Total visitors
    cursor.execute('SELECT COUNT(*) FROM visitor_logs')
    total_visitors = cursor.fetchone()[0]
    
    # Naver Ad visitors
    cursor.execute('SELECT COUNT(*) FROM visitor_logs WHERE is_naver_ad = 1')
    naver_ad_visitors = cursor.fetchone()[0]
    
    # Organic/Other visitors
    organic_visitors = total_visitors - naver_ad_visitors
    
    # Top keywords from Naver ads
    cursor.execute('''
        SELECT naver_keyword, COUNT(*) as count 
        FROM visitor_logs 
        WHERE is_naver_ad = 1 AND naver_keyword IS NOT NULL AND naver_keyword != ''
        GROUP BY naver_keyword 
        ORDER BY count DESC 
        LIMIT 5
    ''')
    top_keywords = [dict(row) for row in cursor.fetchall()]
    
    # Top referrers (excluding main site to find real inflows)
    cursor.execute('''
        SELECT referrer, COUNT(*) as count 
        FROM visitor_logs 
        WHERE referrer IS NOT NULL AND referrer != ''
        GROUP BY referrer 
        ORDER BY count DESC 
        LIMIT 5
    ''')
    top_referrers = [dict(row) for row in cursor.fetchall()]

    # Traffic source by hour (last 24 hours)
    kst_now = datetime.utcnow() + timedelta(hours=9)
    kst_24h_ago = (kst_now - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute('''
        SELECT strftime('%H', timestamp) as hour, 
               SUM(CASE WHEN is_naver_ad = 1 THEN 1 ELSE 0 END) as naver_count,
               SUM(CASE WHEN is_naver_ad = 0 THEN 1 ELSE 0 END) as other_count
        FROM visitor_logs
        WHERE timestamp >= ?
        GROUP BY hour
        ORDER BY hour ASC
    ''', (kst_24h_ago,))
    hourly_traffic = [dict(row) for row in cursor.fetchall()]

    conn.close()
    
    return {
        'total_visitors': total_visitors,
        'naver_ad_visitors': naver_ad_visitors,
        'organic_visitors': organic_visitors,
        'naver_ratio': round((naver_ad_visitors / total_visitors * 100), 1) if total_visitors > 0 else 0,
        'top_keywords': top_keywords,
        'top_referrers': top_referrers,
        'hourly_traffic': hourly_traffic
    }
