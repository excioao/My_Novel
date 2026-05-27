#!/usr/bin/env python3
"""小说角色塑形访谈 —— 终端交互式问卷，回答完毕后自动追加到 人物档案库.md。"""

import os
import sys
from datetime import datetime

QUESTIONS = [
    # ===== 身份基石 =====
    ("身份基石", "姓名是什么？名字有无特殊含义或由来？"),
    ("身份基石", "年龄多大？生理年龄与心理年龄是否一致？"),
    ("身份基石", "出生地在哪？那里的环境如何塑造了 TA？"),
    ("身份基石", "现在住在哪里？居住环境反映 TA 怎样的处境？"),
    ("身份基石", "职业或身份是什么？干这行多久了？是主动选择还是被迫谋生？"),
    ("身份基石", "社会阶层与经济状况如何？这对 TA 的日常选择有多大影响？"),
    # ===== 外在印象 =====
    ("外在印象", "身高、体型、发色、瞳色、肤色？"),
    ("外在印象", "最引人注目的外貌特征是什么？（伤疤、纹身、异色瞳、饰品等）"),
    ("外在印象", "常穿什么风格的衣服？是讲究还是随意？"),
    ("外在印象", "第一印象给陌生人什么感觉？（亲和、冷漠、紧张、强势……）"),
    ("外在印象", "TA 身上有什么习惯性小动作？（咬嘴唇、转笔、拨头发……）"),
    ("外在印象", "声音是什么样的？说话节奏快还是慢？"),
    # ===== 性格内核 =====
    ("性格内核", "用三个形容词概括 TA 的性格。"),
    ("性格内核", "TA 最大的优点是什么？这个优点有没有反噬过 TA？"),
    ("性格内核", "TA 最大的缺点是什么？TA 自己知道这个缺点吗？"),
    ("性格内核", "TA 是内向还是外向？在人群中是充电还是耗电？"),
    ("性格内核", "TA 情绪稳定吗？什么东西最容易引爆 TA 的情绪？"),
    ("性格内核", "TA 面对冲突时是什么反应？（正面刚、回避、冷静斡旋、装死……）"),
    ("性格内核", "TA 最恐惧的事情是什么？这个恐惧根源在哪？"),
    ("性格内核", "TA 最渴望得到什么？（被爱、被认可、权力、自由、复仇……）"),
    # ===== 信念与价值观 =====
    ("信念与价值观", "TA 相信命运吗？还是认为一切靠自己？"),
    ("信念与价值观", "TA 心中的道德底线在哪里？有没有可以被触碰的灰色地带？"),
    ("信念与价值观", "在'正确'和'忠诚'之间，TA 选哪个？"),
    ("信念与价值观", "TA 对谎言的态度是什么？（绝不说谎、必要时说谎、每天都在说谎……）"),
    ("信念与价值观", "如果 TA 只能救一个人 —— 至亲还是一个能拯救世界的陌生人 —— TA 怎么选？"),
    ("信念与价值观", "TA 有什么偏见或盲区？哪些事 TA 觉得自己绝对正确？"),
    # ===== 能力与弱项 =====
    ("能力与弱项", "TA 最擅长什么技能？这个技能是怎么练出来的？"),
    ("能力与弱项", "TA 有什么独门绝活？（格斗、话术、偷窃、编程、做饭……）"),
    ("能力与弱项", "TA 最不擅长什么？会不会因为这个短板吃过亏？"),
    ("能力与弱项", "TA 的智商/情商水平如何？哪个更占优势？"),
    ("能力与弱项", "有没有特殊能力或异能？来源和代价是什么？（若为非奇幻故事可填"无"）"),
    ("能力与弱项", "TA 在战斗/危机中是谋划型、直觉型、还是莽撞型？"),
    # ===== 背景与过往 =====
    ("背景与过往", "童年过得怎样？用一个具体的童年记忆来概括。"),
    ("背景与过往", "父母或抚养者是什么样的人？和 TA 的关系如何？"),
    ("背景与过往", "TA 人生中最重要的转折点是什么？发生在几岁？"),
    ("背景与过往", "TA 做过最后悔的一件事是什么？"),
    ("背景与过往", "TA 有过最骄傲的一个时刻是什么？"),
    ("背景与过往", "有没有改变 TA 一生的人？那个人对 TA 说了什么或做了什么？"),
    # ===== 人际关系 =====
    ("人际关系", "TA 相信别人吗？容易交到朋友还是戒备心很重？"),
    ("人际关系", "TA 怎么对待比自己弱的人？怎么对待比自己强的人？"),
    ("人际关系", "TA 谈过恋爱吗？对爱情是什么态度？"),
    ("人际关系", "TA 在团队中通常扮演什么角色？（领导者、独狼、粘合剂、智囊……）"),
    ("人际关系", "TA 有没有死对头或宿敌？矛盾是怎么结下的？"),
    ("人际关系", "如果有一个人能让 TA 卸下伪装，那个人是谁？为什么？"),
    # ===== 日常与癖好 =====
    ("日常与癖好", "TA 的一天通常怎么过？（从起床到入睡）"),
    ("日常与癖好", "TA 有什么爱好？是发自内心的热爱还是纯粹消磨时间？"),
    ("日常与癖好", "TA 吃什么？有没有独特的饮食习惯？"),
    ("日常与癖好", "TA 睡觉习惯是什么样的？（秒睡、失眠、抱着东西才能睡……）"),
    ("日常与癖好", "TA 有什么古怪的小癖好或迷信行为？"),
    ("日常与癖好", "口袋里或随身包里装着什么东西？翻出来给我们看看。"),
    # ===== 欲望与弧光 =====
    ("欲望与弧光", "TA 想要什么？（表层欲望）"),
    ("欲望与弧光", "TA 真正需要的是什么？（深层需求 ——TA 自己可能都不知道）"),
    ("欲望与弧光", "什么阻碍 TA 得到它？（外在障碍 & 内在障碍）"),
    ("欲望与弧光", "在故事结束时，TA 会发生怎样的改变？或者说 TA 会失去什么、获得什么？"),
    ("欲望与弧光", "如果 TA 最终失败，会是什么原因导致的？"),
    # ===== 作者速写 =====
    ("作者速写", "用一个比喻来形容这个角色。（TA 像什么？一场暴雨、一把生锈的刀、一座闹市中的孤岛……）"),
    ("作者速写", "如果 TA 存在于现实中，读者会在 TA 身上看到谁的影子？"),
    ("作者速写", "写一句 TA 的内心独白，让 TA 自己来说话。"),
]

SECTION_ORDER = [
    "身份基石", "外在印象", "性格内核", "信念与价值观",
    "能力与弱项", "背景与过往", "人际关系", "日常与癖好",
    "欲望与弧光", "作者速写",
]

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "人物档案库.md")


def run_interview() -> list[tuple[str, str, str]]:
    """逐题发问，返回 [(section, question, answer), ...] 列表。"""
    answers: list[tuple[str, str, str]] = []
    total = len(QUESTIONS)

    print("=" * 60)
    print("  角 色 塑 形 访 谈")
    print("=" * 60)
    print(f"\n共 {total} 道题，按 Enter 开始。你可以随时按 Ctrl+C 退出，已回答的内容不会保存。\n")
    input(">>> ")

    for i, (section, question) in enumerate(QUESTIONS, 1):
        print(f"\n[{i}/{total}] 【{section}】{question}")
        answer = input("> ").strip()
        answers.append((section, question, answer))

    print("\n" + "=" * 60)
    print("  访谈结束，正在生成档案……")
    print("=" * 60)
    return answers


def build_markdown(answers: list[tuple[str, str, str]], role_name: str) -> str:
    """将回答组装为 Markdown 文本。"""
    lines: list[str] = []
    lines.append(f"\n---\n")
    lines.append(f"## {role_name}")
    lines.append(f"\n> 录入时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    # 按板块分组
    grouped: dict[str, list[tuple[str, str]]] = {}
    for section, question, answer in answers:
        if not answer:
            continue
        grouped.setdefault(section, []).append((question, answer))

    for section in SECTION_ORDER:
        items = grouped.get(section)
        if not items:
            continue
        lines.append(f"### {section}\n")
        for q, a in items:
            lines.append(f"- **{q}**")
            lines.append(f"  {a}")
        lines.append("")

    return "\n".join(lines)


def append_to_database(markdown: str) -> None:
    """追加 Markdown 内容到角色数据库文件末尾。"""
    with open(DB_PATH, "a", encoding="utf-8") as f:
        f.write(markdown)


def main() -> None:
    os.system("cls" if os.name == "nt" else "clear")

    print("请输入角色姓名（用于在 人物档案库.md 中创建二级标题）：")
    role_name = input("> ").strip()
    if not role_name:
        role_name = "未命名角色"

    os.system("cls" if os.name == "nt" else "clear")
    answers = run_interview()

    markdown = build_markdown(answers, role_name)

    os.system("cls" if os.name == "nt" else "clear")
    print("\n生成的角色档案预览：\n")
    print(markdown)

    print("\n是否将以上内容追加保存到 人物档案库.md？(y/n)")
    confirm = input("> ").strip().lower()
    if confirm in ("y", "yes", "是", ""):
        append_to_database(markdown)
        print("\n角色档案已成功录入！")
    else:
        print("\n已取消保存。")

    print(f"\n文件位置：{DB_PATH}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n访谈已中断，本次回答不会保存。")
        sys.exit(0)
