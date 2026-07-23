#!/usr/bin/env python3
"""
CMS渠道用户反馈数据清洗脚本 v3
主内容列：C列（问题描述）

目标：保留尽可能精简、有效的反馈，并标注情感类型
情感类型：positive / negative / demand（需求/建议）/ neutral

清洗规则（全量）:
  0. 空值/无ID行
  1. 超短过滤：原文+译文综合长度 < 10字（豁免 Bug/Lag/卡/广告等关键词）
  2a. 纯礼貌短词：OK / 谢谢 / Gracias 等无实质内容
  2b. 纯乱码：重复字母、键盘序列、纯数字、纯emoji、纯网址
  2c. 纯名词搜索：youtube / mp3 / music 等单独名词
  2d. 无意义语言单字：阿拉伯/孟加拉/泰文/印地文 超短单字
  3.  搜索行为模式：how to / want to / cara / bagaimana 等
  4.  精确去重：完全相同内容只保留1条
  5.  机型+5分钟+内容去重：同设备短时间内重复提交
  6.  纯夸赞句（无实质问题/建议）：如 "this app is so amazing!" 等
"""

import pandas as pd
import re
import json
from datetime import datetime

# ============================================================
# 豁免关键词（超短内容但有实际意义）
# ============================================================
SHORT_KEEP_KEYWORDS = [
    'ad', 'ads', 'bug', 'lag', 'crash', 'error', 'slow', 'vip', 'pro', 'pay',
    'pop', 'popup', 'fix', 'fail', 'not work', 'broken',
    # Chinese
    '卡', '闪退', '崩溃', '广告', '充值', '付费', '卡顿', '报错', '黑屏',
    # Spanish
    'error', 'lag', 'bug', 'lento', 'falla', 'fallo', 'caída',
    # Arabic
    'مشكلة', 'بطيء', 'خطأ', 'توقف',
    # Russian
    'ошибка', 'лагает', 'крашит',
    # Indonesian
    'error', 'lag', 'crash', 'macet',
]

# ============================================================
# 纯礼貌短词（精确匹配）
# ============================================================
POLITE_WORDS = {
    # English — 单词/极短词（无句子结构）
    'ok', 'okay', 'nice', 'good', 'great', 'best', 'cool', 'wow',
    'thanks', 'thank', 'thank you', 'thx', 'ty', 'pls', 'please',
    'super', 'awesome', 'perfect', 'amazing', 'excellent', 'fantastic', 'wonderful',
    'useful', 'helpful', 'bad', 'no', 'yes', 'nope', 'yep',
    # 两词短语（无主语，仍无实质信息）
    'good app', 'nice app', 'great app', 'best app', 'perfect app',
    'very good', 'very nice', 'very great', 'very useful', 'very helpful',
    'so good', 'so nice', 'so great', 'so amazing', 'so awesome',
    # Chinese — 单字/极短词
    '好', '很好', '棒', '赞', '优秀', '完美', '不错', '谢谢', '感谢',
    '太好了', '非常好', '很不错', '好用', '超好用', '好评', '满意',
    # Spanish / Portuguese
    'si', 'no', 'bien', 'bueno', 'muy bien', 'muy bueno', 'excelente',
    'gracias', 'vale', 'perfecto', 'genial', 'fantastico', 'increible',
    'bom', 'muito bom', 'ótimo', 'obrigado', 'obrigada',
    # French
    'oui', 'non', 'merci', 'bien', 'très bien', 'parfait',
    # Arabic
    'شكرا', 'نعم', 'لا', 'ممتاز', 'جيد', 'رائع',
    # Indonesian / Malay
    'oke', 'iya', 'ya', 'tidak', 'bagus', 'siap', 'makasih',
    'terima kasih', 'mantap', 'keren', 'bagus sekali',
    # Russian
    'ок', 'да', 'нет', 'хорошо', 'спасибо', 'отлично',
    # Bengali / Hindi
    'হ্যাঁ', 'না', 'ঠিক আছে', 'ধন্যবাদ', 'हां', 'नहीं', 'शुक्रिया',
}
# ⚠️ 注意：含主语+谓语的正面完整句（如 "this app is amazing", "i love this app"）
# 不在 POLITE_WORDS 里，应保留为 positive 类反馈。

# ============================================================
# 纯名词搜索型（单独出现 = 搜索行为）
# ============================================================
NOUN_SEARCH_WORDS = {
    'youtube', 'tiktok', 'facebook', 'instagram', 'whatsapp', 'snapchat',
    'telegram', 'netflix', 'spotify', 'twitter', 'x', 'reddit', 'wechat',
    'mp3', 'mp4', 'audio', 'video', 'song', 'songs', 'music', 'movies',
    'ringtone', 'ringtones', 'wallpaper', 'wallpapers', 'photo', 'photos',
    'status', 'stories', 'reels', 'shorts', 'podcast',
}

# ============================================================
# 夸赞型句子模式（只过滤无主语的空洞断言 / 无实质内容的短感叹）
# 规则：有完整主谓结构（"this app is amazing"、"i love this app"）的正面句 → 保留为 positive
# 只清洗：无主语的描述词堆叠，如 "very good app"、"best app ever"、"amazing application"
# ============================================================
PRAISE_ONLY_PATTERNS = [
    # 无主语/冠词开头的形容词堆叠，如 "very amazing app"、"best app ever"
    r'^(very |so |really |absolutely |quite |truly )?(good|great|amazing|awesome|excellent|perfect|fantastic|wonderful|beautiful|best|nice|helpful|useful|superb|brilliant|outstanding)( (app|application|software|tool|program|ever))?[\.\!\s]*$',
    # 纯感谢句，如 "thank you so much for this app"（无具体内容）
    r'^(thank you|thanks|gracias|merci|danke|teşekkür|спасибо|شكرا)([\s,]+(so much|a lot|very much|for (this|the) (app|application))?)?[\.\!\s]*$',
]
# ⚠️ 有主语的完整正面句（如 "this app is amazing"、"i love this app"）不在此列，
# 将被 detect_sentiment 标记为 positive 并保留。

# ============================================================
# 搜索行为模式
# ============================================================
SEARCH_PATTERNS = [
    r'^https?://', r'^@', r'^#',
    r'^how to\s', r'^how do i\s', r'^how can i\s',
    r'^can i\s', r'^where can i\s', r'^where to\s',
    r'^please (download|send|give|help|fix|add)',
    r'^i want to\s', r'^want to\s', r'^i need to\s',
    r'^cara\s', r'^bagaimana\s', r'^comment\s(faire|télécharger)',
    r'^كيف\s', r'^أين\s',
    r'^(download|subscribe|watch|play|listen)\s+\w+',
    r'^\s*(download|song|music|video|mp3|ringtone|audio)\s*$',
]

# ============================================================
# 情感 + 需求 检测
# ============================================================
POSITIVE_WORDS = [
    'good', 'great', 'excellent', 'love', 'amazing', 'best', 'awesome', 'perfect',
    'like', 'beautiful', 'nice', 'helpful', 'thanks', 'thank', 'smooth', 'fast',
    '好', '棒', '赞', '喜欢', '满意', '优秀', '完美', '好用', '流畅',
]
NEGATIVE_WORDS = [
    'bad', 'worst', 'terrible', 'hate', 'slow', 'bug', 'crash', 'error', 'fail',
    'problem', 'issue', 'broken', 'sucks', 'annoying', 'stupid', 'not working',
    'cannot', "can't", 'unable', 'freeze', 'stuck', 'lag', 'laggy', 'hang',
    'black screen', 'no sound', 'no audio', 'lost', 'missing', 'disappeared',
    '差', '烂', '崩溃', '卡', '慢', '难用', '垃圾', '失望', '投诉', '报错', '闪退',
    'tidak bisa', 'tidak mau', 'tidak berjalan', 'error',
    'не работает', 'ошибка', 'зависает', 'лагает',
    'no funciona', 'falla', 'problema', 'error', 'lento',
]
# ============================================================
# 语义过滤：与产品功能/体验无关的反馈（宗教、政治、日常闲聊等）
# ============================================================
IRRELEVANT_PATTERNS = [
    # 宗教/祈祷类
    r'\b(allah|god bless|bismillah|alhamdulillah|subhanallah|inshallah|mashallah|assalamu|praise (the lord|god|allah))\b',
    # 纯祈祷/祝福句（无产品内容）
    r'^(god bless|bless you|may god|may allah|pray for|amen|ameen|subhanallah|alhamdulillah|bismillah)[\s\W]*$',
    # 与产品功能完全无关的话题（政治、足球/体育成绩等）
    r'^(who|what|when|where|why) (is|are|was|were) .{0,40}\?$',  # 纯问答型（非功能问题）
]
# 产品相关关键词白名单：只要含这些词就不算"无关"
PRODUCT_RELEVANT_KEYWORDS = [
    'app', 'application', 'video', 'audio', 'music', 'song', 'download', 'play', 'player',
    'screen', 'record', 'recording', 'file', 'format', 'speed', 'quality', 'sound',
    'crash', 'bug', 'error', 'slow', 'lag', 'freeze', 'update', 'feature', 'setting',
    'button', 'menu', 'icon', 'notification', 'storage', 'battery', 'wifi', 'bluetooth',
    '下载', '播放', '视频', '音频', '音乐', '录屏', '崩溃', '卡顿', '广告', '功能', '设置',
    '应用', '软件', '文件', '格式', '速度', '质量', '声音', '画质', '更新', '版本',
]

DEMAND_WORDS = [
    'please add', 'add feature', 'add a feature', 'need feature', 'missing feature',
    'would be nice', 'would love', 'wish', 'hope', 'suggestion', 'suggest',
    'feature request', 'i want', 'i need', 'can you add', 'can you make',
    'should have', 'should be', 'should also', 'could you', 'why not',
    'please make', 'please fix', 'please update', 'please add',
    '希望', '建议', '能不能', '可以加', '请增加', '请添加', '期望', '想要', '求',
    'tolong tambah', 'mohon', 'harap',
    'пожалуйста добавь', 'хотелось бы', 'можно добавить',
    'por favor añad', 'sería bueno', 'quisiera',
    'j\'aimerais', 'pourriez-vous', 'il faudrait',
]


def has_keep_keyword(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in SHORT_KEEP_KEYWORDS)


def is_url_only(text: str) -> bool:
    return bool(re.match(r'^https?://\S+$', text.strip(), re.I))


def is_pure_number(text: str) -> bool:
    return bool(re.match(r'^[\d\s\.\-\+\(\)\/\\:]{1,25}$', text.strip()))


def is_pure_emoji(text: str) -> bool:
    t = text.strip()
    return len(t) <= 20 and bool(re.match(r'^[\U00010000-\U0010ffff\U0001F000-\U0001FAFF\s]+$', t))


def is_gibberish(text: str) -> bool:
    t = text.strip().lower()
    if len(t) < 4:
        return False
    if re.match(r'^(.)\1{3,}$', t):
        return True
    # 全是同一字符
    if len(set(t.replace(' ', ''))) <= 1:
        return True
    # 键盘行序列
    keyboard_rows = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm', 'йцукенгшщзхъ', 'фывапролджэё']
    if len(t) >= 5:
        for row in keyboard_rows:
            if len(t) >= 5 and all(c in row or c == ' ' for c in t):
                return True
    return False


def is_polite_only(text: str) -> bool:
    """纯礼貌短词（精确匹配或末尾带标点）"""
    t = text.strip().lower()
    t_clean = re.sub(r'[\!\.\,\?\s！。，？]+$', '', t).strip()
    return t in POLITE_WORDS or t_clean in POLITE_WORDS


def is_noun_search(text: str) -> bool:
    """纯名词搜索型"""
    t = re.sub(r'^[!.\,\?\s]+|[!.\,\?\s]+$', '', text.strip().lower())
    return t in NOUN_SEARCH_WORDS


def is_praise_only(text: str) -> bool:
    """
    无主语的空洞夸赞断言（无实质问题/建议），清洗掉。
    有完整主谓结构的正面句（"this app is amazing", "i love this app"）→ 不清洗，保留为 positive。
    """
    t = text.strip().lower()
    # 含主语（i/this/it/the/app）开头的完整正面句 → 直接保留，不走此规则
    if re.match(r'^(i |this |it |the |my )', t):
        return False
    if len(t) > 60:
        return False
    for pat in PRAISE_ONLY_PATTERNS:
        if re.match(pat, t, re.I):
            return True
    return False


def is_search_behavior(text: str) -> bool:
    """搜索行为模式"""
    t = text.strip().lower()
    return any(re.search(pat, t, re.I) for pat in SEARCH_PATTERNS)


def is_repetitive_long(text: str) -> bool:
    """
    超长重复语句：同一个短语/句子反复堆叠，或相同词组重复 5 次以上。
    例：'please fix please fix please fix please fix please fix'
        'I want I want I want I want I want I want'
    """
    t = text.strip()
    if len(t) < 30:
        return False
    words = t.lower().split()
    if len(words) < 8:
        return False
    # 检测重复词组（2-4个词的窗口）
    for window in range(2, 5):
        if len(words) < window * 4:
            continue
        phrase = tuple(words[:window])
        count = sum(1 for i in range(0, len(words) - window + 1, window) if tuple(words[i:i+window]) == phrase)
        if count >= 4 and count * window >= len(words) * 0.6:
            return True
    # 字符级重复：整体字符串有大量重复片段
    half = t[:len(t)//2]
    if half and t.count(half.strip()) >= 3:
        return True
    return False


def is_continuous_gibberish(text: str) -> bool:
    """
    连续乱码/无意义字符序列：
    - 大量连续相同字符（已有is_gibberish覆盖短文本，这里覆盖更长文本）
    - 长度>15的纯随机字母串（无空格，非已知单词）
    - 大量混合符号+字母乱码
    """
    t = text.strip()
    # 超长无空格字母串（>20字符）很可能是乱码
    if re.match(r'^[a-zA-Z]{20,}$', t) and not re.search(r'(ing|tion|ness|ment|able|ful|less|ous|ive|al|er|ed|ly)\b', t, re.I):
        return True
    # 大量连续重复字符（允许长文本）
    if re.search(r'(.)\1{6,}', t):
        return True
    # 高比例非常规字符（符号、乱码混杂）
    non_word = len(re.findall(r'[^a-zA-Z0-9\u4e00-\u9fff\u0600-\u06FF\u0400-\u04FF\u0900-\u097F\s\.\,\!\?\;\:\'\"\-\(\)]', t))
    if len(t) > 10 and non_word / len(t) > 0.4:
        return True
    return False


def is_product_irrelevant(text: str, zh_text: str = '') -> bool:
    """
    与产品功能/体验完全无关的反馈：
    - 纯宗教/祈祷内容
    - 纯闲聊/问候（无产品相关词）
    长度较短的句子更可能是闲聊，不做过度过滤。
    """
    t = text.strip().lower()
    # 产品相关白名单优先通过
    combined = (t + ' ' + zh_text.lower())
    if any(kw in combined for kw in PRODUCT_RELEVANT_KEYWORDS):
        return False
    # 纯宗教/祈祷模式
    for pat in IRRELEVANT_PATTERNS:
        if re.search(pat, t, re.I):
            return True
    return False


def is_meaningless_short_lang(text: str) -> bool:
    """无意义语言单字（阿拉伯/孟加拉/泰/印地文）"""
    t = text.strip()
    patterns = [
        (r'^[\u0600-\u06FF]+$', 5),   # 阿拉伯文
        (r'^[\u0980-\u09FF]+$', 4),   # 孟加拉文
        (r'^[\u0E00-\u0E7F]+$', 4),   # 泰文
        (r'^[\u0900-\u097F]+$', 4),   # 印地文/天城文
        (r'^[\u4E00-\u9FFF]+$', 2),   # 汉字（仅1-2字）
    ]
    if has_keep_keyword(t):
        return False
    for pat, max_len in patterns:
        if re.match(pat, t) and len(t) <= max_len:
            return True
    return False


def detect_sentiment(text: str, zh_text: str = '') -> str:
    """检测情感类型：positive / negative / demand / neutral"""
    combined = (text + ' ' + zh_text).lower()

    # 优先检测需求/建议（demand > negative > positive > neutral）
    demand_score = sum(1 for w in DEMAND_WORDS if w in combined)
    neg_score = sum(1 for w in NEGATIVE_WORDS if w in combined)
    pos_score = sum(1 for w in POSITIVE_WORDS if w in combined)

    if demand_score > 0:
        return 'demand'
    if neg_score > pos_score:
        return 'negative'
    if pos_score > neg_score:
        return 'positive'
    return 'neutral'


def extract_keywords(texts: list, top_n: int = 30) -> list:
    stop_words = {
        'the','a','an','is','are','was','were','be','been','have','has','had',
        'do','does','did','will','would','could','should','may','might','can',
        'need','to','of','in','for','on','with','at','by','from','up','about',
        'into','through','after','before','between','and','but','if','or',
        'that','which','who','this','it','its','i','me','my','we','you','your',
        'he','she','they','them','his','her','their','what','how','when',
        'please','want','download','video','music','videos','como','que','para',
        'di','ke','yang','ini','dan','tidak','ada','untuk','dari','saya',
        'pero','con','una','este','esta','muy','je','le','la','les','des',
        'une','avec','pas','plus','tout','bien',
    }
    freq = {}
    for text in texts:
        for w in re.findall(r'[a-zA-Z]{4,}', text.lower()):
            if w not in stop_words:
                freq[w] = freq.get(w, 0) + 1
    return sorted(freq.items(), key=lambda x: -x[1])[:top_n]


def parse_time(val):
    if pd.isna(val):
        return None
    s = str(val).strip()
    for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M:%S',
                '%Y/%m/%d %H:%M', '%Y-%m-%d']:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


# ============================================================
# 主清洗函数
# ============================================================
def clean_and_analyze(filepath: str) -> dict:
    print(f"\n{'='*60}")
    print(f"📁 读取: {filepath}")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print('='*60)

    df = pd.read_excel(filepath, header=0)
    # 去掉无 ID 的空行
    df = df[df.iloc[:, 0].notna() & (df.iloc[:, 0].astype(str).str.strip() != '')]
    total = len(df)
    print(f"\n📊 有ID数据行: {total} 条")

    cols = list(df.columns)

    def find_col(*candidates):
        """按候选表头名顺序查找，返回第一个匹配的列名，找不到返回 None"""
        for name in candidates:
            if name in cols:
                return name
        return None

    col_id      = cols[0]   # 第一列始终是 ID
    # 国家/地区：CMS=「地区」，GP=「源语言」
    col_region  = find_col('地区', '源语言', 'region', 'country', 'language') or cols[1]
    # 主内容原文：CMS=「问题描述」，GP=「评价内容」
    col_content = find_col('问题描述', '评价内容', 'content', 'description', 'feedback') or cols[2]
    # 中文译文：CMS=「问题描述(中文)」，GP=「评价内容(中文)」或同名带括号变体
    col_zh      = find_col('问题描述(中文)', '评价内容(中文)', '中文译文', 'content_zh', 'translation') or cols[3]
    # 功能模块
    col_module  = find_col('功能模块', 'module', 'category') or cols[7]
    # 问题类型
    col_type    = find_col('问题类型', '反馈类型', 'type', 'issue_type') or cols[8]
    # 机型：CMS=「机型」，GP=「设备名称」
    col_device  = find_col('机型', '设备名称', 'device', 'model', 'phone') or cols[9]
    # 应用版本：CMS=「应用版本」，GP=「版本名称」
    col_version = find_col('应用版本', '版本名称', 'version', 'app_version') or cols[10]
    # 提交时间
    col_time    = find_col('提交时间', 'submit_time', 'time', 'date', '时间') or cols[15]

    print(f"📋 字段映射: 地区={col_region} | 内容={col_content} | 机型={col_device} | 版本={col_version} | 时间={col_time}")

    stats = {k: 0 for k in [
        'empty', 'ultra_short', 'polite', 'gibberish', 'url', 'number',
        'emoji', 'noun_search', 'lang_short', 'search_behavior',
        'praise_only', 'duplicate', 'device_time_dup',
        'repetitive', 'cont_gibberish', 'irrelevant',
    ]}

    records = []
    seen = {}  # content.lower() -> True

    for _, row in df.iterrows():
        raw   = str(row[col_content]).strip() if pd.notna(row[col_content]) else ''
        zh    = str(row[col_zh]).strip()      if pd.notna(row[col_zh])      else ''
        dev   = str(row[col_device]).strip()  if pd.notna(row[col_device])  else ''
        t_raw = row[col_time]
        raw_len = len(raw)
        zh_len  = len(zh)

        keep   = True
        reason = None

        # 0. 空值
        if not raw or raw == 'nan':
            reason = '空值'; keep = False; stats['empty'] += 1

        # 1. 超短（原文<10字 且 译文<8字，豁免含关键词的）
        elif raw_len < 10 and zh_len < 8 and not has_keep_keyword(raw):
            reason = '超短内容'; keep = False; stats['ultra_short'] += 1

        # 2a. 纯礼貌短词
        elif is_polite_only(raw) and keep:
            reason = '纯礼貌词'; keep = False; stats['polite'] += 1

        # 2b. 乱码
        elif is_gibberish(raw):
            reason = '纯乱码'; keep = False; stats['gibberish'] += 1

        elif is_url_only(raw):
            reason = '纯URL'; keep = False; stats['url'] += 1

        elif is_pure_number(raw):
            reason = '纯数字'; keep = False; stats['number'] += 1

        elif is_pure_emoji(raw):
            reason = '纯emoji'; keep = False; stats['emoji'] += 1

        # 2c. 纯名词搜索
        elif is_noun_search(raw):
            reason = '纯名词搜索'; keep = False; stats['noun_search'] += 1

        # 2d. 无意义语言单字
        elif is_meaningless_short_lang(raw):
            reason = '无意义单字'; keep = False; stats['lang_short'] += 1

        # 3. 搜索行为
        elif is_search_behavior(raw):
            reason = '搜索行为'; keep = False; stats['search_behavior'] += 1

        # 3b. 超长重复语句
        elif is_repetitive_long(raw):
            reason = '重复堆叠语句'; keep = False; stats['repetitive'] += 1

        # 3c. 连续乱码（长文本）
        elif is_continuous_gibberish(raw):
            reason = '连续乱码'; keep = False; stats['cont_gibberish'] += 1

        # 3d. 与产品无关
        elif is_product_irrelevant(raw, zh):
            reason = '与产品无关'; keep = False; stats['irrelevant'] += 1

        # 4. 精确去重
        elif raw.lower() in seen:
            reason = '重复内容'; keep = False; stats['duplicate'] += 1

        if keep:
            seen[raw.lower()] = True

        # 质量分级
        quality = ''
        sentiment = ''
        if keep:
            effective_len = max(raw_len, zh_len)
            if effective_len >= 40:   quality = 'A'
            elif effective_len >= 15: quality = 'B'
            else:                     quality = 'C'
            sentiment = detect_sentiment(raw, zh)

        records.append({
            'original_id':  row[col_id],
            'region':       row[col_region] if pd.notna(row[col_region]) else '',
            'content_raw':  raw,
            'content_zh':   zh,
            'module':       row[col_module]  if pd.notna(row[col_module])  else '',
            'problem_type': row[col_type]    if pd.notna(row[col_type])    else '',
            'device':       dev,
            'version':      row[col_version] if pd.notna(row[col_version]) else '',
            'submit_time':  t_raw,
            'submit_dt':    parse_time(t_raw),
            'keep':         keep,
            'reason':       reason,
            'quality':      quality,
            'sentiment':    sentiment,
            'char_count':   raw_len,
        })

    df1 = pd.DataFrame(records)
    kept1 = df1[df1['keep'] == True].copy()
    print(f"📊 一阶段（规则清洗）保留: {len(kept1)} 条")

    # 5. 机型+时间桶(5分钟)+内容 去重
    seen2 = {}
    for i, row in kept1.iterrows():
        dt  = row['submit_dt']
        dev = row['device'].lower()
        c   = row['content_raw'].lower()
        bucket = None
        if dt is not None:
            bucket = dt.replace(minute=(dt.minute//5)*5, second=0, microsecond=0)
        key = (dev, bucket, c)
        if key in seen2:
            df1.loc[i, 'keep']   = False
            df1.loc[i, 'reason'] = '同机型5min内重复'
            stats['device_time_dup'] += 1
        else:
            seen2[key] = True

    # 6. 纯夸赞句（在保留数据中再过滤）
    praise_removed = 0
    for i, row in df1[df1['keep'] == True].iterrows():
        if is_praise_only(row['content_raw']):
            df1.loc[i, 'keep']   = False
            df1.loc[i, 'reason'] = '纯夸赞句'
            stats['praise_only'] += 1
            praise_removed += 1

    kept    = df1[df1['keep'] == True].copy()
    removed = df1[df1['keep'] == False].copy()
    print(f"📊 二阶段（设备去重+纯夸赞过滤）保留: {len(kept)} 条")

    # ============================================================
    # 报告
    # ============================================================
    print(f"\n{'='*60}")
    print("🧹 清洗结果")
    print('='*60)
    print(f"原始:   {total:>5} 条")
    print(f"保留:   {len(kept):>5} 条 ({len(kept)/total*100:.1f}%)")
    print(f"去除:   {len(removed):>5} 条 ({len(removed)/total*100:.1f}%)")

    label_map = {
        'empty':'空值', 'ultra_short':'超短内容', 'polite':'纯礼貌词',
        'gibberish':'纯乱码', 'url':'纯URL', 'number':'纯数字',
        'emoji':'纯emoji', 'noun_search':'纯名词搜索',
        'lang_short':'无意义单字', 'search_behavior':'搜索行为',
        'praise_only':'纯夸赞句', 'duplicate':'重复内容',
        'device_time_dup':'同机型5min重复',
        'repetitive':'重复堆叠语句', 'cont_gibberish':'连续乱码',
        'irrelevant':'与产品无关',
    }
    print("\n清洗明细:")
    for k, v in stats.items():
        if v > 0:
            print(f"  {label_map.get(k,k):<20}: {v:>5}条 ({v/total*100:.1f}%)")

    sent = kept['sentiment'].value_counts()
    qual = kept['quality'].value_counts()
    print("\n情感分布:")
    for s, c in sent.items():
        icons = {'positive':'👍','negative':'👎','demand':'💡','neutral':'😐'}
        print(f"  {icons.get(s,s)} {s}: {c}条 ({c/len(kept)*100:.1f}%)")

    print("\n质量分布:")
    for q, c in qual.items():
        lbl = {'A':'⭐高质量(>=40字)','B':'📊中等(15-39字)','C':'📝低质量(<15字)'}
        print(f"  {lbl.get(q,q)}: {c}条")

    rc = kept['region'].value_counts().head(10)
    print("\n地区Top10:")
    for r, c in rc.items():
        print(f"  {r}: {c}")

    mc = kept['module'].value_counts().head(10)
    print("\n功能模块Top10:")
    for m, c in mc.items():
        print(f"  {m}: {c}")

    keywords = extract_keywords(kept['content_raw'].tolist(), 30)

    # ============================================================
    # 导出
    # ============================================================
    base = filepath.replace('.xlsx', '')

    export_df = kept[['original_id','region','content_raw','content_zh',
                       'module','problem_type','device','version',
                       'submit_time','quality','sentiment','char_count']].copy()
    export_df.columns = ['ID','地区','原始内容','中文译文','功能模块','问题类型',
                         '机型','应用版本','提交时间','质量等级','情感','字符数']
    export_df.to_excel(base + '_cleaned_v3.xlsx', index=False)

    removed[['original_id','region','content_raw','content_zh',
             'module','submit_time','reason']].copy().to_excel(
        base + '_removed_v3.xlsx', index=False)

    # 平台 JSON
    json_records = []
    for _, row in kept.iterrows():
        date_str = str(row['submit_time']).split(' ')[0] if row['submit_time'] else ''
        json_records.append({
            'id':         str(row['original_id']),
            'user':       str(row['region']) or '匿名用户',
            'region':     str(row['region']) or '',
            'product':    str(row['module'])[:40] if row['module'] else '未知',
            'content':    str(row['content_raw']),
            'content_zh': str(row['content_zh']),
            'rating':     3,
            'date':       date_str,
            'sentiment':  str(row['sentiment']),
            'channel':    str(row['problem_type']) or '',
            'quality':    str(row['quality']),
            'device':     str(row['device']) or '',
            'version':    str(row['version']) or '',
            'char_count': int(row['char_count']),
        })

    # 隐私边界：公开平台数据(feedback_data.json)绝不携带联系方式等 PII。
    # 注意：本地标注产物 weekly_downloads/*_labeled 仍保留 contact，供内部跟进使用。
    _PII_KEYS = ('contact', '联系方式', 'email', 'phone', '邮箱', '手机', '电话')
    for _r in json_records:
        for _k in _PII_KEYS:
            _r.pop(_k, None)

    json_path = '/Users/shswhuangyi/Desktop/workbuddy/cms_feedback/feedback_data.json'
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_records, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 保留数据: {base}_cleaned_v3.xlsx ({len(export_df)}条)")
    print(f"✅ 被清洗:  {base}_removed_v3.xlsx ({len(removed)}条)")
    print(f"✅ 平台JSON: {json_path} ({len(json_records)}条)")

    return {
        'total': total, 'kept': len(kept), 'removed': len(removed),
        'stats': stats, 'quality': qual.to_dict(), 'sentiment': sent.to_dict(),
        'region_top10': rc.to_dict(), 'module_top10': mc.to_dict(), 'keywords': keywords,
    }


if __name__ == '__main__':
    import sys
    filepath = sys.argv[1] if len(sys.argv) > 1 else '/Users/shswhuangyi/Downloads/cms渠道 (12).xlsx'
    clean_and_analyze(filepath)
