#!/usr/bin/env python3
"""
run_workflow.py -- dual-model writing pipeline.
Director (DeepSeek V4 Pro) issues mission cards. Writer (Kimi K2.5) generates prose.
Python-layer audit with multiscale word-chain sniffing, format-fingerprint cleaning,
dynamic threshold adjustment, chapter word-count enforcement, and hook validation.
"""

import os
import re
import math
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field

from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parent
VAULT = ROOT / "草稿箱"
SKILL_DIR = ROOT / "小说创作技巧库"
AGENT_01 = SKILL_DIR / "智能体_01_指挥审计官.md"
AGENT_02 = SKILL_DIR / "智能体_02_文学创作者.md"
SKILL_12 = SKILL_DIR / "12_多尺度文本特征指纹拦截.md"
SKILL_13 = SKILL_DIR / "13_章节结构与连载节奏规范.md"
SKILL_00 = SKILL_DIR / "00_顶级历史小说语感与审美总纲.md"
SKILL_14 = SKILL_DIR / "14_唯物主义情感互动规范.md"
SKILL_15 = SKILL_DIR / "15_张力制造去模板化规范.md"
SKILL_16 = SKILL_DIR / "16_章节级张弛节奏与呼吸章规范.md"
SKILL_17 = SKILL_DIR / "17_标点符号使用规范.md"
MASTER_OUTLINE = ROOT / "主大纲.md"
LEDGER = ROOT / "历史考据库" / "沈节密核大帅府暗账流水.md"

DS_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-5bfdd589212b498b967cee3b205f41a3")
DS_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DS_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")

KM_API_KEY = os.getenv("KIMI_API_KEY", "sk-3oK1CJeWnWfkas2jaobUtRDzN8Wcu0RBvnXNc3lRQpOgNo4Q")
KM_BASE_URL = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
KM_MODEL = os.getenv("KIMI_MODEL", "kimi-k2.5")

MAX_RETRY = 3
BASE_CV_THRESHOLD = 0.60
BASE_NGRAM_LIMIT = 3
BASE_NUMERIC_MIN = 2

NGRAM_BLACKLIST = [
    "不仅仅是", "更是要", "可以说", "可谓是", "不难看", "不难发", "由此可", "通过以", "综上所",
    "不仅仅是更", "拉开了序幕", "见证了历史", "在这片土地", "不可否认的", "显而易见的",
    "值得注意的", "深入探讨了", "不仅仅是更是", "拉开了新序幕", "见证了这一历",
    "在这片土地上", "不可否认的是", "显而易见的是", "不仅仅是更是对", "拉开了全新序幕",
    "见证了这一历史", "在这片古老土地上", "赋能", "抓手", "闭环", "对齐", "拉通",
    "打通", "链路", "颗粒度", "维度", "底层逻辑",
]

FORMAT_PREAMBLE_RE = re.compile(r"^(好的[，,]|明白了[，,]|让我来写|以下是根据|为您创作)")
FORMAT_PAREN_RE = re.compile(r"[（(][^）)]{3,}[）)]")
FORMAT_SUMMARY_TAIL_TRIGGERS = [
    "这意味", "由此可见", "综上", "总而言之", "通过以上", "到这里", "说明了", "启示", "告诉我们",
]
FORMAT_ENUMERATION_RE = re.compile(r"(首先.{0,30})(其次.{0,30})(最后.{0,30})")

HOOK_FORBIDDEN = [
    "他不知道的是", "这一刻的变化", "暗流正在涌动", "命运的齿轮",
    "不久后", "仅仅是开始", "正是这种力量", "在这片大地上",
    "历史的长河", "时代的洪流", "谁又能想到", "谁又能说得清",
    "又有谁能", "何曾想过", "生活就是这样", "人这一生", "有些路", "有些事",
    "夜色依旧", "风还在吹", "月光洒在", "大雪仍然", "天地间一片",
    "这意味", "由此可见", "综上", "总而言之", "通过以上分析",
]

BEAT_INTENSITY = {"引入": 0, "建立": 1, "复杂化": 2, "对抗": 3, "解决": 0}

ABSTRACT_MARKERS = [
    "正义", "邪恶", "命运", "宿命", "绝望", "希望",
    "信仰", "背叛", "忠诚", "权力", "救赎", "毁灭",
]

FATAL_WORDS_RE = re.compile(r"赋能|抓手|闭环|对齐|拉通|打通|链路|颗粒度|维度|底层逻辑")
WARN_WORDS_RE = re.compile(
    r"地狱|绝望|冷酷|铁血|震撼|笼罩|讽刺|致敬"
    r"|不仅仅是|更是|系统|机制|框架"
)

ROMANCE_CLICHE_RE = re.compile(
    r"喜欢|爱慕|倾心|动心|动情|心动|心跳漏了一拍|小鹿乱撞|心猿意马|一往情深"
    r"|脸红|耳赤|脸颊发烫|心跳加速|心跳漏了拍|呼吸一窒|浑身酥麻|电击一般"
    r"|一股暖流|电流传遍全身|温柔的眼神|深情的目光|含情脉脉|眼波流转"
    r"|目光缠绵|眼神里藏着爱意|眸中带情|充满爱意的眼神|心里一暖"
    r"|涌起一阵甜蜜|莫名的心安|说不清的情愫|隐隐约约的情意"
    r"|依偎|拥抱|情难自禁|不愿松开"
)


@dataclass
class AuditResult:
    passed: bool = False
    fatal_violations: list[str] = field(default_factory=list)
    warn_violations: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    sentence_cv: float = 0.0
    numeric_count: int = 0
    sensory_channels: int = 0
    em_dash_count: int = 0
    not_but_count: int = 0
    ngram_hits: int = 0
    forbidden_hits: dict[str, int] = field(default_factory=dict)
    format_fingerprints: list[str] = field(default_factory=list)
    chapter_word_count: int = 0


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_all_skills() -> str:
    blocks = []
    for f in sorted(SKILL_DIR.glob("*.md")):
        if "智能体" in f.name:
            continue
        blocks.append(f"# {f.stem}\n\n{load_text(f)}")
    return "\n\n---\n\n".join(blocks)


def build_director_system() -> str:
    return (
        load_text(AGENT_01)
        + "\n\n---\n\n## 审美总纲\n" + load_text(SKILL_00)
        + "\n\n---\n\n## 主大纲\n" + load_text(MASTER_OUTLINE)
        + "\n\n---\n\n## 暗账流水\n" + load_text(LEDGER)
        + "\n\n---\n\n## 指纹拦截与章节规范\n"
        + load_text(SKILL_12) + "\n\n---\n\n" + load_text(SKILL_13)
        + "\n\n---\n\n## 情感互动规范\n" + load_text(SKILL_14)
        + "\n\n---\n\n## 张力制造去模板化\n" + load_text(SKILL_15)
        + "\n\n---\n\n## 章节张弛节奏\n" + load_text(SKILL_16)
        + "\n\n---\n\n## 标点符号规范\n" + load_text(SKILL_17)
    )


def build_writer_system() -> str:
    return (
        load_text(AGENT_02)
        + "\n\n---\n\n## 审美总纲\n" + load_text(SKILL_00)
        + "\n\n---\n\n## 全部创作规范\n" + load_all_skills()
        + "\n\n---\n\n## 指纹拦截与章节规范\n"
        + load_text(SKILL_12) + "\n\n---\n\n" + load_text(SKILL_13)
        + "\n\n---\n\n## 情感互动规范\n" + load_text(SKILL_14)
        + "\n\n---\n\n## 张力制造去模板化\n" + load_text(SKILL_15)
        + "\n\n---\n\n## 章节张弛节奏\n" + load_text(SKILL_16)
        + "\n\n---\n\n## 标点符号规范\n" + load_text(SKILL_17)
    )


def sentence_cv(text: str) -> float:
    sents = [s.strip() for s in re.split(r"[。！？\n]+", text) if s.strip()]
    if len(sents) < 3:
        return 1.0
    mean = sum(len(s) for s in sents) / len(sents)
    if mean == 0:
        return 0.0
    var = sum((len(s) - mean) ** 2 for s in sents) / len(sents)
    return math.sqrt(var) / mean


def extract_beat_label(text: str) -> str:
    m = re.search(r"节拍标签[：:]\s*[\[【]?(\S+?)[\]】]?", text)
    return m.group(1) if m else "建立"


def count_sensory_channels(text: str) -> int:
    channels = 0
    if re.search(r"看|望|盯|注视|目光|视|色|光|暗|亮|漆黑|白茫", text): channels += 1
    if re.search(r"听|声|响|鸣|吱|闷|低|吼|喊|说|道|问|答", text): channels += 1
    if re.search(r"冷|热|暖|冻|冰|凉|烫|温|麻|刺|痛|痒|触|摸|碰|摁|按|握|抓", text): channels += 1
    if re.search(r"闻|臭|腥|锈|霉|焦|熏|膻|酸|苦", text): channels += 1
    if re.search(r"温度|寒冷|寒风|风雪|冻土|冰壳|冰柱|零下", text): channels += 1
    return channels


def count_not_but(text: str) -> int:
    return len(re.findall(r"不是.{0,20}而是", text))


def count_em_dash(text: str) -> int:
    return text.count("—") + text.count("——")


def detect_pov_breach(text: str) -> list[str]:
    breaches = []
    for p in [
        r"他知道.{0,30}意味", r"她意识到.{0,30}命运",
        r"他隐隐感觉到", r"他后来会知道", r"命运似乎在暗示",
        r"仿佛.{0,10}在告诉他",
    ]:
        m = re.search(p, text)
        if m:
            breaches.append(f"POV盲区跨越: {m.group()[:40]}")
    return breaches


def detect_consecutive_pronouns(text: str) -> bool:
    sents = [s.strip() for s in re.split(r"[。！？\n]+", text) if s.strip()]
    streak = 0
    for s in sents:
        if re.match(r"^[他她它]", s) and 20 <= len(s) <= 40:
            streak += 1
            if streak >= 4:
                return True
        else:
            streak = 0
    return False


def detect_abstract_not_but(text: str) -> int:
    pattern = re.compile(r"不是.{0,20}而是.{0,50}")
    abstract_count = 0
    for m in pattern.findall(text):
        if sum(1 for w in ABSTRACT_MARKERS if w in m) >= 2:
            abstract_count += 1
    return abstract_count


def detect_summary_tails(text: str) -> int:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    count = 0
    for p in paragraphs[-2:]:
        if any(t in p for t in FORMAT_SUMMARY_TAIL_TRIGGERS):
            count += 1
    return count


def count_numerics(text: str) -> int:
    total = 0
    for p in [
        r"\d+[石斤两斗升丈尺寸分亩匹头只口个]",
        r"[一二三四五六七八九十百千万亿]+[石斤两斗升丈尺寸]",
        r"\d+\.\d+",
    ]:
        total += len(re.findall(p, text))
    return total


def count_chapter_words(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text)) + len(re.findall(r"[a-zA-Z]+", text))


def scan_ngrams(text: str) -> tuple[int, list[str]]:
    hits = []
    total = 0
    for phrase in NGRAM_BLACKLIST:
        count = text.count(phrase)
        if count > 0:
            hits.append(f"'{phrase}' x{count}")
            total += count
    return total, hits


def scan_format_fingerprints(text: str) -> list[str]:
    fps = []
    if FORMAT_PREAMBLE_RE.match(text.lstrip()):
        fps.append("前置客套话: 首段以AI默认开场白开头")
    parens = FORMAT_PAREN_RE.findall(text)
    if parens:
        fps.append(f"括号内剧本式批注: {len(parens)} 处 → {parens[:2]}")
    if FORMAT_ENUMERATION_RE.search(text):
        fps.append("首先-其次-最后三段式分点陈列")
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if paragraphs:
        last = paragraphs[-1]
        for t in FORMAT_SUMMARY_TAIL_TRIGGERS:
            if t in last:
                fps.append(f"后置总结尾音: 末段含'{t}'")
                break
    return fps


def detect_chapter_end_hook(text: str) -> tuple[bool, str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) < 2:
        return False, "段落数不足，无法检测钩子"
    last = paragraphs[-1]
    for h in HOOK_FORBIDDEN:
        if h in last:
            return False, f"章末钩子含禁用句式: '{h}'"
    if len(last) < 15:
        return False, f"章末钩子过短 ({len(last)} 字)"
    if len(last) > 200:
        return False, f"章末钩子过长 ({len(last)} 字)"
    return True, "ok"


def detect_romance_cliches(text: str) -> list[str]:
    hits = ROMANCE_CLICHE_RE.findall(text)
    if not hits:
        return []
    freq = {}
    for h in hits:
        freq[h] = freq.get(h, 0) + 1
    return [f"工业糖精: '{w}' x{c}" for w, c in freq.items()]


def get_dynamic_thresholds(retry_round: int) -> tuple[float, int, int]:
    if retry_round == 0:
        return BASE_CV_THRESHOLD, BASE_NGRAM_LIMIT, BASE_NUMERIC_MIN
    elif retry_round == 1:
        return 0.70, 2, 3
    elif retry_round == 2:
        return 0.80, 1, 4
    else:
        return 0.80, 1, 4


def audit(text: str, beat_label: str = "建立", retry_round: int = 0) -> AuditResult:
    cv_limit, ngram_limit, numeric_min = get_dynamic_thresholds(retry_round)
    kilo_words = max(count_chapter_words(text), 1) / 1000.0

    result = AuditResult()
    result.sentence_cv = sentence_cv(text)
    result.numeric_count = count_numerics(text)
    result.sensory_channels = count_sensory_channels(text)
    result.em_dash_count = count_em_dash(text)
    result.not_but_count = count_not_but(text)
    result.chapter_word_count = count_chapter_words(text)
    result.ngram_hits, ngram_details = scan_ngrams(text)
    result.format_fingerprints = scan_format_fingerprints(text)

    intensity = BEAT_INTENSITY.get(beat_label, 1)

    if result.sentence_cv < cv_limit:
        result.fatal_violations.append(
            f"句长变异系数 {result.sentence_cv:.2f} 低于动态阈值 {cv_limit}（驳回轮次={retry_round}）"
        )

    for b in detect_pov_breach(text):
        result.fatal_violations.append(b)

    romance_hits = detect_romance_cliches(text)
    for rh in romance_hits:
        result.fatal_violations.append(f"情感描写违规: {rh}")

    if result.numeric_count < numeric_min:
        result.fatal_violations.append(
            f"数目字 {result.numeric_count} 个，低于动态下限 {numeric_min}"
        )

    cw = result.chapter_word_count
    if cw < 2500:
        result.fatal_violations.append(f"章节字数 {cw} 低于绝对下限 2500")
    elif cw > 3800:
        result.fatal_violations.append(f"章节字数 {cw} 超过绝对上限 3800")

    fatal_word_hits = FATAL_WORDS_RE.findall(text)
    if fatal_word_hits:
        result.fatal_violations.append(f"致命禁词: {', '.join(set(fatal_word_hits))}")

    if result.ngram_hits / kilo_words > ngram_limit:
        result.warn_violations.append(
            f"词链密度 {result.ngram_hits / kilo_words:.1f}/千字，超动态上限 {ngram_limit}"
            f"（命中 {result.ngram_hits} 处: {ngram_details[:5]}）"
        )

    if result.format_fingerprints:
        for fp in result.format_fingerprints:
            result.warn_violations.append(f"格式指纹: {fp}")

    hook_ok, hook_msg = detect_chapter_end_hook(text)
    if not hook_ok:
        result.warn_violations.append(f"章末钩子: {hook_msg}")

    warn_words = WARN_WORDS_RE.findall(text)
    warn_freq = {}
    for w in warn_words:
        warn_freq[w] = warn_freq.get(w, 0) + 1
    result.forbidden_hits = warn_freq
    over_limit = [w for w, c in warn_freq.items() if c / kilo_words > 1.0]
    if over_limit:
        result.warn_violations.append(f"警告禁词频次超标: {', '.join(over_limit)}")

    if detect_summary_tails(text) >= 2:
        result.warn_violations.append("连续段落总结尾音触发")

    if detect_abstract_not_but(text) >= 1:
        result.warn_violations.append(f"抽象'不是A而是B'用例")

    if detect_consecutive_pronouns(text):
        result.warn_violations.append("连续四句以上他/她开头且句长均落20-40字窄带")

    if result.sensory_channels < 2:
        result.warn_violations.append(f"感官通道仅 {result.sensory_channels} 个")

    if result.not_but_count > 3:
        result.warn_violations.append(f"'不是A而是B'全场景 {result.not_but_count} 次")

    if result.em_dash_count / kilo_words > 2.0:
        result.advisories.append(f"破折号每千字 {result.em_dash_count / kilo_words:.1f} 个")

    if result.not_but_count > 2:
        result.advisories.append(f"'不是A而是B'句式密度偏高 ({result.not_but_count} 次)")

    n_fatal = len(result.fatal_violations)
    n_warn = len(result.warn_violations)
    result.passed = (n_fatal == 0) and (n_warn < 3)
    return result


def build_rejection_note(result: AuditResult, iteration: int) -> str:
    lines = ["## 审计驳回", f"### 第 {iteration} 次驳回"]
    if result.fatal_violations:
        lines.append("### 致命违规")
        for i, v in enumerate(result.fatal_violations, 1):
            lines.append(f"{i}. {v}")
    if result.warn_violations:
        lines.append(f"### 警告累计（{len(result.warn_violations)} 条）")
        for i, v in enumerate(result.warn_violations, 1):
            lines.append(f"{i}. {v}")
    if result.advisories:
        lines.append("### 本场建议（不触发驳回）")
        for i, v in enumerate(result.advisories, 1):
            lines.append(f"{i}. {v}")
    lines.append("### 修改指令")
    lines.append("致命违规逐条物理替代。警告累计检查聚集位置。词链超标精简冗余词组。格式指纹清洗括号批注和总结尾音。章末钩子用物理物体的不完整状态替换抽象感受句。")
    return "\n".join(lines)


def extract_scene_label(text: str) -> str:
    m = re.search(r"## 任务卡：(.+?) -", text)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"任务卡[：:]\s*(.+?)[\n-]", text)
    return m2.group(1).strip() if m2 else f"scene_{datetime.now().strftime('%Y%m%d%H%M%S')}"


def save_chapter(chapter_num: int, prose: str, retries: int) -> Path:
    stamped = (
        f"## 第{chapter_num}章\n"
        f"> 审计通过 / {datetime.now().strftime('%Y-%m-%d %H:%M')} / 驳回 {retries} 次\n\n"
        f"{prose}\n"
    )
    target = VAULT / f"第{chapter_num:02d}章.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(stamped)
    return target


def commit_chapter(chapter_num: int, retries: int) -> None:
    status = "pass" if retries == 0 else f"pass-r{retries}" if retries < MAX_RETRY else f"forced-r{retries}"
    msg = f"feat: chapter {chapter_num:02d} generated via DeepSeek-Kimi pipeline ({status})"
    try:
        for args in [
            ["git", "config", "--local", "user.name", "Excioao"],
            ["git", "config", "--local", "user.email", "3127613845@qq.com"],
            ["git", "add", "."],
            ["git", "commit", "-m", msg],
            ["git", "-c", "http.sslBackend=openssl", "push", "origin", "master"],
        ]:
            subprocess.run(args, cwd=str(ROOT), check=True, capture_output=True)
        print(f"  [git] committed & pushed: {msg}")
    except subprocess.CalledProcessError:
        print(f"  [git] push failed (network), chapter saved locally")


async def chat(client: AsyncOpenAI, model: str, system: str, prompt: str, temperature: float = 0.7) -> str:
    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


async def run_one_scene(ds: AsyncOpenAI, km: AsyncOpenAI, scene_input: str) -> tuple[str, int]:
    dir_sys = build_director_system()
    wrt_sys = build_writer_system()

    card = await chat(ds, DS_MODEL, dir_sys,
                      f"为以下场景生成一张完整的任务卡：\n\n{scene_input}", temperature=0.3)
    draft = await chat(km, KM_MODEL, wrt_sys,
                       f"请根据以下任务卡撰写正文初稿：\n\n{card}", temperature=1.0)
    beat = extract_beat_label(card if card else draft)
    report = audit(draft, beat, retry_round=0)
    iteration = 0

    while not report.passed and iteration < MAX_RETRY:
        iteration += 1
        rejection = build_rejection_note(report, iteration)
        rewrite_prompt = (
            f"## 原始任务卡\n{card}\n\n## {rejection}\n\n## 上一轮正文（需修正）\n{draft}"
        )
        draft = await chat(km, KM_MODEL, wrt_sys, rewrite_prompt, temperature=1.0)
        report = audit(draft, beat, retry_round=iteration)

    return draft, iteration


async def main_loop(scenes: list[str]) -> None:
    ds = AsyncOpenAI(api_key=DS_API_KEY, base_url=DS_BASE_URL)
    km = AsyncOpenAI(api_key=KM_API_KEY, base_url=KM_BASE_URL)
    total = len(scenes)
    try:
        for idx, scene in enumerate(scenes):
            # extract chapter number from scene description
            m = re.match(r"第(\d+)章", scene)
            ch_num = int(m.group(1)) if m else idx + 1
            bar = "█" * (idx + 1) + "░" * (total - idx - 1) if total > 1 else "█"
            print(f"\n{'='*50}\n[{bar}] 第{ch_num}章 / 共{total}章\n{'='*50}")
            print("  [DeepSeek] 下发任务卡...")
            prose, retries = await run_one_scene(ds, km, scene)
            save_chapter(ch_num, prose, retries)
            s = "pass" if retries == 0 else f"pass(r={retries})" if retries < MAX_RETRY else f"forced({retries})"
            print(f"  [Kimi] 第{ch_num}章完成: {s}")
            commit_chapter(ch_num, retries)
    finally:
        await ds.close()
        await km.close()
    print(f"\n{'='*50}\n[{'█'*total}] {total}/{total} done")


def main():
    asyncio.run(main_loop([
        "第6章——进大帅府。沈节骑马抵达大帅府，被安排在军机书房等候。主母顾韫隔着一张木桌看了他整整十息。桌面上一盏油灯，灯捻烧焦了半截，没有人去拨。主母开口说的第一句话是：'你在锦衣卫的代号是什么。'多写沈节的生理反应——心率变化、碎铁片在胸腔里的感觉、手指在膝盖上停了一息没动。多写主母的面部控制和书房里的物理细节。",
    ]))


if __name__ == "__main__":
    main()
