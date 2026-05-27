#!/usr/bin/env python3
"""
运行智能体流水线.py —— 指挥审计官（DeepSeek）与文学创作者（Kimi-k2.5）双模型异步博弈写作文本生产线。
每场场景经由 任务卡下发 → 初稿生成 → 审计驳回循环 → 物理落盘 四阶段。
"""

import os
import re
import json
import time
import math
import subprocess
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None


ROOT = Path(__file__).resolve().parent
草稿箱 = ROOT / "草稿箱"
SKILL_DIR = ROOT / "小说创作技巧库"
AGENT_01 = SKILL_DIR / "智能体_01_指挥审计官.md"
AGENT_02 = SKILL_DIR / "智能体_02_文学创作者.md"
主大纲 = ROOT / "主大纲.md"
暗账流水 = ROOT / "历史考据库" / "沈节密核大帅府暗账流水.md"

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

KIMI_API_KEY = os.getenv("KIMI_API_KEY", "")
KIMI_BASE_URL = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
KIMI_MODEL = os.getenv("KIMI_MODEL", "moonshot-v1-8k")

MAX_RETRY = 3
句长变异阈值 = 0.6

禁词正则 = re.compile(
    r"(地狱|绝望|冷酷|铁血|致敬|讽刺|震撼|笼罩"
    r"|赋能|抓手|闭环|对齐|拉通|打通|链路"
    r"|颗粒度|维度|底层逻辑|系统机制|框架|不仅仅是|更是)"
)

破折号上限每千字 = 1


def _加载文本(path: Path) -> str:
    """读取本地文件全部文本。"""
    raw = path.read_text(encoding="utf-8")
    return raw


def _加载所有技能() -> str:
    skills = []
    for f in sorted(SKILL_DIR.glob("*.md")):
        if "智能体" in f.name:
            continue
        skills.append(f"# {f.stem}\n\n{_加载文本(f)}")
    return "\n\n---\n\n".join(skills)


def _加载审计官系统上下文() -> str:
    核心 = _加载文本(AGENT_01)
    大纲 = _加载文本(主大纲)
    流水 = _加载文本(暗账流水)
    return f"{核心}\n\n---\n\n## 主大纲\n{大纲}\n\n---\n\n## 暗账流水\n{流水}"


def _加载创作者系统上下文() -> str:
    核心 = _加载文本(AGENT_02)
    全技能 = _加载所有技能()
    return f"{核心}\n\n---\n\n## 全部创作规范\n{全技能}"


async def _调用模型(
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> str:
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


def _句长变异系数(text: str) -> float:
    sentences = re.split(r"[。！？\n]+", text)
    lengths = [len(s.strip()) for s in sentences if s.strip()]
    if len(lengths) < 3:
        return 1.0
    mean = sum(lengths) / len(lengths)
    if mean == 0:
        return 0.0
    variance = sum((l - mean) ** 2 for l in lengths) / len(lengths)
    return math.sqrt(variance) / mean


def _检测禁词(text: str) -> list[str]:
    return [m.group() for m in 禁词正则.finditer(text)]


def _检测三段式尾音(text: str) -> bool:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) < 2:
        return False
    last = paragraphs[-1]
    triggers = ["这意味", "由此可见", "通过以上", "综上", "总而言之", "一句话总结", "到这里", "说明了", "告诉我们", "启示"]
    return any(t in last for t in triggers)


def _检测抽象不是而是(text: str) -> bool:
    pattern = re.compile(r"不是.{0,20}而是.{0,50}")
    matches = pattern.findall(text)
    abstract_markers = ["正义", "邪恶", "命运", "宿命", "绝望", "希望", "信仰", "背叛", "忠诚", "权力"]
    count = 0
    for m in matches:
        hits = sum(1 for w in abstract_markers if w in m)
        if hits >= 2:
            count += 1
    return count >= 2


def _检测合理数(text: str) -> int:
    count = 0
    patterns = [r"\d+[石斤两斗升丈尺寸分亩匹头只口]", r"[一二三四五六七八九十百千万亿]+[石斤两斗升丈尺寸]", r"\d+\.\d+"]
    for p in patterns:
        count += len(re.findall(p, text))
    return count


def _审计正文(text: str) -> list[str]:
    violations = []
    cv = _句长变异系数(text)
    if cv < 句长变异阈值:
        violations.append(f"句长变异系数 {cv:.2f}，低于阈值 {句长变异阈值}。需打碎句长正态分布。")
    bad_words = _检测禁词(text)
    if bad_words:
        violations.append(f"禁词命中: {', '.join(set(bad_words))}。替换为可度量物理事实。")
    if _检测三段式尾音(text):
        violations.append("段末检测到总结尾音触发词。砍掉末段最后一到两句抽象拔高。")
    if _检测抽象不是而是(text):
        violations.append("检测到至少两处'不是A而是B'两侧均为抽象概念的用例。重写为物理对比或直接拆除该句式。")
    数目字 = _检测合理数(text)
    if 数目字 < 3:
        violations.append(f"数目字仅 {数目字} 个，不足三个。每个场景必须嵌入至少三个可核验的精确数字或物理量。")
    return violations


def _生成驳回单(violations: list[str], iteration: int) -> str:
    lines = ["## 审计驳回", "", f"### 第 {iteration} 次驳回", ""]
    for i, v in enumerate(violations, 1):
        lines.append(f"{i}. {v}")
    lines.append("")
    lines.append("### 修改指令")
    lines.append("逐条执行以上违规项的物理替代。禁词替换为传感器可检测的物体、动作或生理变化。")
    lines.append("句长重排——在页面上制造至少一处三至五字极短句和一处八十至一百二十字长白描因果链句。")
    lines.append("")
    lines.append("### 追加上下文")
    lines.append("本场景视点角色此刻的全部已知信息仅限于任务卡中'本场景盲区'未列出的部分。")
    lines.append("任何超出该盲区边界的信息均为泄漏——删除。")
    return "\n".join(lines)


def _获取任务卡场景名(text: str) -> str:
    m = re.search(r"## 任务卡：(.+?) -", text)
    if m:
        return m.group(1).strip()
    return f"未命名_{datetime.now().strftime('%Y%m%d%H%M%S')}"


def _追加正文到草稿箱(scene_name: str, 正文: str, iteration: int) -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    content = f"\n\n---\n## 场景：{scene_name}\n> 通过审计 / 生成时间：{timestamp} / 驳回次数：{iteration}\n\n{正文}\n"
    filepath = 草稿箱 / "chapter_01.md"
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(content)
    return filepath


def _静默推送() -> None:
    try:
        subprocess.run(["git", "config", "--local", "user.name", "Excioao"], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run(["git", "config", "--local", "user.email", "3127613845@qq.com"], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "refactor: complete full-chinese multi-agent workflow update using deepseek and kimi"], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run(["git", "push", "-c", "http.sslBackend=openssl", "origin", "master"], cwd=str(ROOT), check=True, capture_output=True)
    except subprocess.CalledProcessError:
        pass


async def _单场场景流水线(
    指挥客户端: AsyncOpenAI,
    创作客户端: AsyncOpenAI,
    场景描述: str,
) -> tuple[str, int]:
    审计上下文 = _加载审计官系统上下文()
    创作上下文 = _加载创作者系统上下文()

    任务卡 = await _调用模型(
        client=指挥客户端,
        model=DEEPSEEK_MODEL,
        system_prompt=审计上下文,
        user_prompt=f"请为以下场景生成一张完整的任务卡：\n\n{场景描述}",
        temperature=0.3,
    )
    scene = _获取任务卡场景名(任务卡)
    本轮正文 = ""
    iteration = 0

    while iteration < MAX_RETRY:
        iteration += 1
        if iteration == 1:
            user_prompt = f"请根据以下任务卡撰写正文初稿：\n\n{任务卡}"
        else:
            user_prompt = f"## 原始任务卡\n{任务卡}\n\n## 上一轮正文\n{本轮正文}\n\n## {驳回单}"

        本轮正文 = await _调用模型(
            client=创作客户端,
            model=KIMI_MODEL,
            system_prompt=创作上下文,
            user_prompt=user_prompt,
            temperature=0.6,
        )
        violations = _审计正文(本轮正文)
        if not violations:
            break
        驳回单 = _生成驳回单(violations, iteration)

    return 本轮正文, iteration


async def _主循环(场景队列: list[str]) -> None:
    if not DEEPSEEK_API_KEY or not KIMI_API_KEY:
        print("缺少 DEEPSEEK_API_KEY 或 KIMI_API_KEY 环境变量。流水线无法启动。")
        return

    指挥客户端 = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    创作客户端 = AsyncOpenAI(api_key=KIMI_API_KEY, base_url=KIMI_BASE_URL)

    for idx, 场景 in enumerate(场景队列, 1):
        正文, 驳回次数 = await _单场场景流水线(指挥客户端, 创作客户端, 场景)
        scene_name = f"{idx:02d}_{_获取任务卡场景名(正文) if 正文 else 'error'}"
        _追加正文到草稿箱(scene_name, 正文, 驳回次数)
        status = "通过" if 驳回次数 < MAX_RETRY else f"强制通过(驳回{驳回次数}次已达上限)"
        print(f"场景 {idx}: {status}")

    _静默推送()
    print("完工")


def main():
    asyncio.run(_主循环([
        "沈节在军机书房整理崇祯十四年九月暗账，碎铁片第一次感知到位移。",
    ]))


if __name__ == "__main__":
    main()
