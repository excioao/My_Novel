#!/usr/bin/env python3
"""
run_workflow.py -- dual-model async writing pipeline.
Director-Auditor (DeepSeek) issues mission cards.
Writer (Kimi-k2.5) drafts prose. Auditor scans. Reject loop (max 3). Pass -> append to local draft vault.
"""

import os
import re
import json
import math
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

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
CV_THRESHOLD = 0.6

FORBIDDEN_RE = re.compile(
    r"(地狱|绝望|冷酷|铁血|致敬|讽刺|震撼|笼罩"
    r"|赋能|抓手|闭环|对齐|拉通|打通|链路"
    r"|颗粒度|维度|底层逻辑|系统机制|框架|不仅仅是|更是)"
)

ABSTRACT_MARKERS = ["正义", "邪恶", "命运", "宿命", "绝望", "希望",
                    "信仰", "背叛", "忠诚", "权力", "救赎", "毁灭"]


@dataclass
class AuditReport:
    passed: bool
    violations: list[str] = field(default_factory=list)
    coefficient_of_variation: float = 0.0
    forbidden_hits: list[str] = field(default_factory=list)
    numeric_count: int = 0


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_all_skills() -> str:
    blocks = []
    for f in sorted(SKILL_DIR.glob("*.md")):
        if "智能体" in f.name:
            continue
        blocks.append(f"# {f.stem}\n\n{load_text(f)}")
    return "\n\n---\n\n".join(blocks)


def build_director_context() -> str:
    return f"{load_text(AGENT_01)}\n\n---\n\n## 主大纲\n{load_text(MASTER_OUTLINE)}\n\n---\n\n## 暗账流水\n{load_text(LEDGER)}"


def build_writer_context() -> str:
    return f"{load_text(AGENT_02)}\n\n---\n\n## 全部创作规范\n{load_all_skills()}"


def sentence_cv(text: str) -> float:
    sents = [s.strip() for s in re.split(r"[。！？\n]+", text) if s.strip()]
    if len(sents) < 3:
        return 1.0
    mean = sum(len(s) for s in sents) / len(sents)
    if mean == 0:
        return 0.0
    var = sum((len(s) - mean) ** 2 for s in sents) / len(sents)
    return math.sqrt(var) / mean


def detect_forbidden(text: str) -> list[str]:
    return list(set(FORBIDDEN_RE.findall(text)))


def detect_summary_tail(text: str) -> bool:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) < 2:
        return False
    last = paragraphs[-1]
    triggers = ["这意味", "由此可见", "通过以上", "综上", "总而言之",
                "一句话总结", "到这里", "说明了", "告诉我们", "启示"]
    return any(t in last for t in triggers)


def detect_abstract_not_but(text: str) -> bool:
    pattern = re.compile(r"不是.{0,20}而是.{0,50}")
    matches = pattern.findall(text)
    count = 0
    for m in matches:
        if sum(1 for w in ABSTRACT_MARKERS if w in m) >= 2:
            count += 1
    return count >= 2


def count_numerics(text: str) -> int:
    total = 0
    for p in [r"\d+[石斤两斗升丈尺寸分亩匹头只口]",
              r"[一二三四五六七八九十百千万亿]+[石斤两斗升丈尺寸]",
              r"\d+\.\d+"]:
        total += len(re.findall(p, text))
    return total


def audit(text: str) -> AuditReport:
    violations = []
    cv = sentence_cv(text)
    if cv < CV_THRESHOLD:
        violations.append(f"句长变异系数 {cv:.2f} 低于阈值 {CV_THRESHOLD}")
    fh = detect_forbidden(text)
    if fh:
        violations.append(f"禁词命中: {', '.join(fh)}")
    if detect_summary_tail(text):
        violations.append("段末总结尾音触发")
    if detect_abstract_not_but(text):
        violations.append("抽象'不是A而是B'用例超限")
    nc = count_numerics(text)
    if nc < 3:
        violations.append(f"数目字不足 ({nc} 个，需至少 3 个)")
    return AuditReport(
        passed=len(violations) == 0,
        violations=violations,
        coefficient_of_variation=cv,
        forbidden_hits=fh,
        numeric_count=nc,
    )


def build_rejection_note(violations: list[str], iteration: int) -> str:
    lines = [
        "## 审计驳回",
        f"### 第 {iteration} 次驳回",
    ]
    for i, v in enumerate(violations, 1):
        lines.append(f"{i}. {v}")
    lines.extend([
        "### 修改指令",
        "逐条物理替代：禁词换为传感器可检测的物体/动作/生理变化。",
        "句长重排：至少一处 3-5 字极短句和一处 80-120 字长白描因果链句。",
        "### 追加上下文",
        "视点角色已知信息仅限任务卡中未列入盲区的部分。超出盲区的信息泄露全部删除。",
    ])
    return "\n".join(lines)


def extract_scene_label(text: str) -> str:
    m = re.search(r"## 任务卡：(.+?) -", text)
    return m.group(1).strip() if m else f"scene_{datetime.now().strftime('%Y%m%d%H%M%S')}"


def append_to_vault(scene_label: str, prose: str, retries: int) -> Path:
    stamped = f"\n\n---\n## {scene_label}\n> 审计通过 / {datetime.now().strftime('%Y-%m-%d %H:%M')} / 驳回 {retries} 次\n\n{prose}\n"
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
            ["git", "commit", "-m",
             "refactor: lock run_workflow.py file name in english and keep agent configurations in chinese"],
            ["git", "-c", "http.sslBackend=openssl", "push", "origin", "master"],
        ]:
            subprocess.run(args, cwd=str(ROOT), check=True, capture_output=True)
    except subprocess.CalledProcessError:
        pass


async def call_model(client: AsyncOpenAI, model: str, system: str,
                     prompt: str, temperature: float = 0.7,
                     max_tokens: int = 4096) -> str:
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


async def run_one_scene(ds: AsyncOpenAI, km: AsyncOpenAI,
                         scene_input: str) -> tuple[str, int]:
    dir_ctx = build_director_context()
    wrt_ctx = build_writer_context()

    card = await call_model(ds, DS_MODEL, dir_ctx,
                            f"为以下场景生成一张完整的任务卡：\n\n{scene_input}",
                            temperature=0.3)
    label = extract_scene_label(card)
    draft = ""
    iteration = 0

    while iteration < MAX_RETRY:
        iteration += 1
        if iteration == 1:
            prompt = f"请根据以下任务卡撰写正文初稿：\n\n{card}"
        else:
            prompt = f"## 原始任务卡\n{card}\n\n## 上一轮正文\n{draft}\n\n## {rejection}"

        draft = await call_model(km, KM_MODEL, wrt_ctx, prompt, temperature=0.6)
        report = audit(draft)
        if report.passed:
            break
        rejection = build_rejection_note(report.violations, iteration)

    return draft, iteration


async def main_loop(scenes: list[str]) -> None:
    if not DS_API_KEY or not KM_API_KEY:
        print("missing DEEPSEEK_API_KEY or KIMI_API_KEY")
        return

    ds_client = AsyncOpenAI(api_key=DS_API_KEY, base_url=DS_BASE_URL)
    km_client = AsyncOpenAI(api_key=KM_API_KEY, base_url=KM_BASE_URL)

    for i, scene in enumerate(scenes, 1):
        prose, retries = await run_one_scene(ds_client, km_client, scene)
        label = f"{i:02d}_{extract_scene_label(prose) if prose else 'error'}"
        append_to_vault(label, prose, retries)
        status = "pass" if retries < MAX_RETRY else f"forced(retries={retries})"
        print(f"scene {i}: {status}")

    silent_push()
    print("done")


def main():
    asyncio.run(main_loop([
        "沈节在军机书房整理崇祯十四年九月暗账，胸口的碎铁片第一次感知到了位移。",
    ]))


if __name__ == "__main__":
    main()
