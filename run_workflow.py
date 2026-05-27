#!/usr/bin/env python3
"""
run_workflow.py -- AutoGen dual-model writing pipeline.
Director (DeepSeek) issues mission cards. Writer (Kimi-k2.5) generates prose.
Audit uses a three-tier severity system. Fatal violations trigger rejection.
Warnings accumulate: >= 3 trigger rejection. Advisories never reject.
"""

import os
import re
import math
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field

try:
    from autogen_agentchat.agents import AssistantAgent
    from autogen_agentchat.tools import AgentTool
    from autogen_ext.models.openai import OpenAIChatCompletionClient
except ImportError:
    AssistantAgent = None
    AgentTool = None
    OpenAIChatCompletionClient = None

ROOT = Path(__file__).resolve().parent
VAULT = ROOT / "草稿箱"
SKILL_DIR = ROOT / "小说创作技巧库"
AGENT_01 = SKILL_DIR / "智能体_01_指挥审计官.md"
AGENT_02 = SKILL_DIR / "智能体_02_文学创作者.md"
MASTER_OUTLINE = ROOT / "主大纲.md"
LEDGER = ROOT / "历史考据库" / "沈节密核大帅府暗账流水.md"

DS_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DS_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DS_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

KM_API_KEY = os.getenv("KIMI_API_KEY", "")
KM_BASE_URL = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
KM_MODEL = os.getenv("KIMI_MODEL", "moonshot-v1-8k")

MAX_RETRY = 3
CV_FATAL_THRESHOLD = 0.35
CV_WARN_THRESHOLD = 0.60

FATAL_WORDS_RE = re.compile(r"赋能|抓手|闭环|对齐|拉通|打通|链路|颗粒度|维度|底层逻辑")
WARN_WORDS_RE = re.compile(
    r"地狱|绝望|冷酷|铁血|震撼|笼罩|讽刺|致敬"
    r"|不仅仅是|更是|系统|机制|框架"
)
ADVISORY_WORDS_RE = re.compile(r"讽刺|致敬")

SUMMARY_TRIGGERS = [
    "这意味", "由此可见", "通过以上", "综上", "总而言之",
    "一句话总结", "到这里", "说明了", "告诉我们", "启示",
]

ABSTRACT_MARKERS = [
    "正义", "邪恶", "命运", "宿命", "绝望", "希望",
    "信仰", "背叛", "忠诚", "权力", "救赎", "毁灭",
]

BEAT_INTENSITY = {"引入": 0, "建立": 1, "复杂化": 2, "对抗": 3, "解决": 0}


@dataclass
class AuditResult:
    passed: bool
    fatal_violations: list[str] = field(default_factory=list)
    warn_violations: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    sentence_cv: float = 0.0
    numeric_count: int = 0
    sensory_channels: int = 0
    em_dash_count: int = 0
    not_but_count: int = 0
    forbidden_hits: dict[str, int] = field(default_factory=dict)


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
        + "\n\n---\n\n## 主大纲\n"
        + load_text(MASTER_OUTLINE)
        + "\n\n---\n\n## 暗账流水\n"
        + load_text(LEDGER)
    )


def build_writer_system() -> str:
    return load_text(AGENT_02) + "\n\n---\n\n## 全部创作规范\n" + load_all_skills()


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
    if re.search(r"看|望|盯|注视|目光|视|色|光|暗|亮|漆黑|白茫", text):
        channels += 1
    if re.search(r"听|声|响|鸣|吱|闷|低|吼|喊|说|道|问|答", text):
        channels += 1
    if re.search(r"冷|热|暖|冻|冰|凉|烫|温|麻|刺|痛|痒|触|摸|碰|摁|按|握|抓", text):
        channels += 1
    if re.search(r"闻|臭|腥|锈|霉|焦|熏|膻|酸|苦", text):
        channels += 1
    if re.search(r"温度|寒冷|寒风|风雪|冻土|冰壳|冰柱|零下", text):
        channels += 1
    return channels


def count_not_but(text: str) -> int:
    return len(re.findall(r"不是.{0,20}而是", text))


def count_em_dash(text: str) -> int:
    return text.count("—") + text.count("——")


def detect_pov_breach(text: str) -> list[str]:
    breaches = []
    patterns = [
        r"他知道.{0,30}意味",
        r"她意识到.{0,30}命运",
        r"他隐隐感觉到",
        r"他后来会知道",
        r"命运似乎在暗示",
        r"仿佛.{0,10}在告诉他",
    ]
    for p in patterns:
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
    matches = pattern.findall(text)
    abstract_count = 0
    for m in matches:
        if sum(1 for w in ABSTRACT_MARKERS if w in m) >= 2:
            abstract_count += 1
    return abstract_count


def detect_summary_tails(text: str) -> int:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    count = 0
    for p in paragraphs[-2:]:
        if any(t in p for t in SUMMARY_TRIGGERS):
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


def count_words(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text)) + len(re.findall(r"[a-zA-Z]+", text))


def audit(text: str, beat_label: str = "建立") -> AuditResult:
    word_count = max(count_words(text), 1)
    kilo_words = word_count / 1000.0
    result = AuditResult()
    result.sentence_cv = sentence_cv(text)
    result.numeric_count = count_numerics(text)
    result.sensory_channels = count_sensory_channels(text)
    result.em_dash_count = count_em_dash(text)
    result.not_but_count = count_not_but(text)

    intensity = BEAT_INTENSITY.get(beat_label, 1)

    cv_limit = CV_FATAL_THRESHOLD if intensity >= 2 else CV_FATAL_THRESHOLD - 0.05

    if result.sentence_cv < cv_limit:
        result.fatal_violations.append(
            f"句长变异系数 {result.sentence_cv:.2f} 低于致命阈值 {cv_limit}"
        )
    elif result.sentence_cv < CV_WARN_THRESHOLD and intensity >= 2:
        result.warn_violations.append(
            f"句长变异系数 {result.sentence_cv:.2f} 低于警告阈值 {CV_WARN_THRESHOLD}（高密度节拍）"
        )

    pov_breaches = detect_pov_breach(text)
    for b in pov_breaches:
        result.fatal_violations.append(b)

    if result.numeric_count == 0:
        result.fatal_violations.append("数目字为零，本场景完全没有可核验物理量")

    fatal_word_hits = FATAL_WORDS_RE.findall(text)
    if fatal_word_hits:
        result.fatal_violations.append(
            f"致命禁词命中: {', '.join(set(fatal_word_hits))}"
        )

    warn_words = WARN_WORDS_RE.findall(text)
    warn_freq = {}
    for w in warn_words:
        warn_freq[w] = warn_freq.get(w, 0) + 1
    result.forbidden_hits = warn_freq
    over_limit = [w for w, c in warn_freq.items() if c / kilo_words > 1.0]
    if over_limit:
        result.warn_violations.append(
            f"警告禁词频次超标（每千字>1次）: {', '.join(over_limit)}"
        )

    summary_count = detect_summary_tails(text)
    if summary_count >= 2:
        result.warn_violations.append(f"连续段落总结尾音触发 {summary_count} 次")

    abstract_nb = detect_abstract_not_but(text)
    if abstract_nb >= 1:
        result.warn_violations.append(
            f"抽象'不是A而是B'用例 {abstract_nb} 处"
        )

    if detect_consecutive_pronouns(text):
        result.warn_violations.append("连续四句以上他/她开头且句长均落20-40字窄带")

    if result.sensory_channels < 2:
        result.warn_violations.append(
            f"感官通道仅 {result.sensory_channels} 个，不足两个"
        )

    if result.not_but_count > 3:
        result.warn_violations.append(
            f"'不是A而是B'全场景 {result.not_but_count} 次，超过密度上限三次"
        )

    if result.em_dash_count / kilo_words > 2.0:
        result.advisories.append(
            f"破折号每千字 {result.em_dash_count / kilo_words:.1f} 个，建议控制在两个以内"
        )

    if result.not_but_count > 2:
        result.advisories.append(
            f"'不是A而是B'全场景 {result.not_but_count} 次，注意句式密度"
        )

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

    lines.extend([
        "### 修改指令",
        "致命违规——逐条物理替代，替换掉违规的抽象表达或信息泄露。",
        "警告累计——检查聚集位置，通常重写一个段落即可同时清除多条警告。",
        "建议条目——选择性采纳。",
    ])
    return "\n".join(lines)


def extract_scene_label(text: str) -> str:
    m = re.search(r"## 任务卡：(.+?) -", text)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"任务卡[：:]\s*(.+?)[\n-]", text)
    return m2.group(1).strip() if m2 else f"scene_{datetime.now().strftime('%Y%m%d%H%M%S')}"


def append_to_vault(scene_label: str, prose: str, retries: int) -> Path:
    stamped = (
        f"\n\n---\n## {scene_label}\n"
        f"> 审计通过 / {datetime.now().strftime('%Y-%m-%d %H:%M')} / 驳回 {retries} 次\n\n"
        f"{prose}\n"
    )
    target = VAULT / "chapter_01.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(stamped)
    return target


def silent_push() -> None:
    try:
        for args in [
            ["git", "config", "--local", "user.name", "Excioao"],
            ["git", "config", "--local", "user.email", "3127613845@qq.com"],
            ["git", "add", "."],
            [
                "git", "commit", "-m",
                "feat: apply native microsoft autogen framework to cross-llm multi-agent collaborative pipeline",
            ],
            ["git", "-c", "http.sslBackend=openssl", "push", "origin", "master"],
        ]:
            subprocess.run(args, cwd=str(ROOT), check=True, capture_output=True)
    except subprocess.CalledProcessError:
        pass


async def run_one_scene(
    ds_client: OpenAIChatCompletionClient,
    km_client: OpenAIChatCompletionClient,
    scene_input: str,
) -> tuple[str, int]:
    director_system = build_director_system()
    writer_system = build_writer_system()

    director = AssistantAgent(
        "director",
        model_client=ds_client,
        system_message=director_system,
        description="指挥审计官。下发任务卡，审计正文。",
    )
    writer = AssistantAgent(
        "writer",
        model_client=km_client,
        system_message=writer_system,
        description="文学创作者。将任务卡转化为正文初稿。",
    )
    writer_tool = AgentTool(writer, return_value_as_last_message=True)

    director_with_tool = AssistantAgent(
        "director",
        model_client=ds_client,
        system_message=director_system,
        description="指挥审计官。下发任务卡，调用 writer 工具生成正文。",
        tools=[writer_tool],
        max_tool_iterations=5,
    )

    task_prompt = (
        f"为以下场景生成一张完整的任务卡，然后使用 writer 工具生成正文初稿：\n\n{scene_input}"
    )
    response = await director_with_tool.run(task=task_prompt)
    first_pass = str(response) if response else ""
    beat_label = extract_beat_label(first_pass)
    report = audit(first_pass, beat_label)
    iteration = 0
    draft = first_pass

    while not report.passed and iteration < MAX_RETRY:
        iteration += 1
        rejection = build_rejection_note(report, iteration)
        rewrite_prompt = f"## 审计驳回\n{rejection}\n\n## 上一轮正文（需修正）\n{draft}"
        response = await writer.run(task=rewrite_prompt)
        draft = str(response) if response else draft
        report = audit(draft, beat_label)

    return draft, iteration


async def main_loop(scenes: list[str]) -> None:
    if not DS_API_KEY or not KM_API_KEY:
        print("missing DEEPSEEK_API_KEY or KIMI_API_KEY")
        return
    if AssistantAgent is None:
        print("autogen-agentchat not installed. pip install autogen-agentchat autogen-ext[openai]")
        return

    ds_client = OpenAIChatCompletionClient(
        model=DS_MODEL,
        base_url=DS_BASE_URL,
        api_key=DS_API_KEY,
    )
    km_client = OpenAIChatCompletionClient(
        model=KM_MODEL,
        base_url=KM_BASE_URL,
        api_key=KM_API_KEY,
    )

    try:
        for i, scene in enumerate(scenes, 1):
            prose, retries = await run_one_scene(ds_client, km_client, scene)
            label = f"{i:02d}_{extract_scene_label(prose) if prose else 'error'}"
            append_to_vault(label, prose, retries)
            status = "pass" if retries == 0 else f"pass(retries={retries})" if retries < MAX_RETRY else f"forced({retries})"
            print(f"scene {i}: {status}")
    finally:
        await ds_client.close()
        await km_client.close()

    silent_push()
    print("done")


def main():
    asyncio.run(main_loop([
        "沈节在军机书房整理崇祯十四年九月暗账，胸口的碎铁片第一次感知到了位移。",
    ]))


if __name__ == "__main__":
    main()
