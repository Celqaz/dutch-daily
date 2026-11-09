"""Travel Japanese block: a date-driven 18-day survival course (Kansai trip).

Unlike the news lessons, this block has no RSS source. The day's theme and its
source phrases are curated here in :data:`TRAVEL_DAYS` (transcribed from the
user's study plan), and the LLM turns them into a one-page lesson in Chinese
(5 core sentences + mini dialogue + number/price practice + listening quiz +
reading challenge). The day is derived from the date: day 1 starts on
``TRAVEL_JA_START``, and after the 18 days the course simply cycles for review.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from . import prompts, render
from .config import Config
from .lesson import complete_json


@dataclass(frozen=True)
class TravelDay:
    day: int
    title: str
    focus: str
    phrases: tuple[tuple[str, str, str], ...]  # (japanese, romaji, chinese)


# Phrases transcribed from the user's plan: (japanese, romaji, chinese). The
# LLM is asked to supply the kana reading for every sentence it keeps.

TRAVEL_DAYS: tuple[TravelDay, ...] = (
    TravelDay(
        1,
        "假名热身 + 万能句（上）",
        "片假名突击（外来语）+ 万能句 1–7，先背熟最通用的救命句",
        (
            ("すみません", "sumimasen", "不好意思 / 打扰一下"),
            ("お願いします", "onegai shimasu", "拜托了"),
            ("これをください", "kore o kudasai", "请给我这个（指东西）"),
            ("～はどこですか", "~ wa doko desu ka", "～在哪里"),
            ("いくらですか", "ikura desu ka", "多少钱"),
            ("～はありますか", "~ wa arimasu ka", "有～吗"),
            ("大丈夫です", "daijoubu desu", "不用了 / 没关系"),
        ),
    ),
    TravelDay(
        2,
        "数字与价格",
        "0–10000、百/千/万组合读法 + 万能句 8–14；练熟价格报数",
        (
            ("ありがとうございます", "arigatou gozaimasu", "谢谢"),
            ("はい / いいえ", "hai / iie", "是 / 不是"),
            ("わかりません", "wakarimasen", "我不懂"),
            ("ゆっくり話してください", "yukkuri hanashite kudasai", "请说慢一点"),
            ("もう一度お願いします", "mou ichido onegai shimasu", "请再说一遍"),
            ("英語を話せますか", "eigo o hanasemasu ka", "会说英语吗"),
            ("日本語が少しだけです", "nihongo ga sukoshi dake desu", "我只会一点点日语"),
            ("1–10", "ichi, ni, san, yon, go, roku, nana, hachi, kyuu, juu", "一～十"),
            ("百 / 千 / 万", "hyaku / sen / man", "百 / 千 / 万"),
            ("円", "en", "日元"),
        ),
    ),
    TravelDay(
        3,
        "数量词",
        "一つ/二つ、一人/二人；把万能句做成能说出口的「说」卡",
        (
            ("一つ / 二つ", "hitotsu / futatsu", "一个 / 两个"),
            ("一人 / 二人", "hitori / futari", "一人 / 两人"),
            ("これ、一つお願いします", "kore, hitotsu onegai shimasu", "这个来一份"),
            ("二人です", "futari desu", "两位"),
        ),
    ),
    TravelDay(
        4,
        "复习 + 初听",
        "听懂「いらっしゃいませ / 少々お待ちください / ～円です」",
        (
            ("いらっしゃいませ", "irasshaimase", "欢迎光临"),
            ("少々お待ちください", "shoushou omachi kudasai", "请稍等"),
            ("～円です", "~ en desu", "一共～日元"),
            ("ありがとうございました", "arigatou gozaimashita", "谢谢（店员常说）"),
        ),
    ),
    TravelDay(
        5,
        "餐厅（上）",
        "入店、点单、要水/茶：餐厅场景前半",
        (
            ("二人です", "futari desu", "两位"),
            ("予約があります", "yoyaku ga arimasu", "我有预约"),
            ("～で予約しました", "~ de yoyaku shimashita", "用「～」的名字订的"),
            ("メニューをください", "menyuu o kudasai", "请给我菜单"),
            ("おすすめは何ですか", "osusume wa nan desu ka", "有什么推荐"),
            ("これ、一つお願いします", "kore, hitotsu onegai shimasu", "这个来一份"),
            ("お水をください", "o-mizu o kudasai", "请给我水"),
            ("お茶をください", "o-cha o kudasai", "请给我茶"),
        ),
    ),
    TravelDay(
        6,
        "餐厅（下）",
        "忌口说明、结账、道谢：餐厅场景后半",
        (
            ("辛いのは苦手です", "karai no wa nigate desu", "不太能吃辣"),
            ("アレルギーはありません", "arerugii wa arimasen", "我没有过敏"),
            ("お会計お願いします", "o-kaikei onegai shimasu", "请结账"),
            ("別々でお願いします", "betsubetsu de onegai shimasu", "分开结账"),
            ("ごちそうさまでした", "gochisousama deshita", "多谢款待（饭后说）"),
        ),
    ),
    TravelDay(
        7,
        "交通（上）",
        "问路、问站台、问这趟车：交通场景前半",
        (
            ("～駅はどこですか", "~ eki wa doko desu ka", "车站在哪"),
            ("～へ行きたいです", "~ e ikitai desu", "我想去～"),
            ("この電車は～に行きますか", "kono densha wa ~ ni ikimasu ka", "这趟车去～吗"),
            ("乗り換えは必要ですか", "norikae wa hitsuyou desu ka", "需要换乘吗"),
            ("何番線ですか", "nanbansen desu ka", "在几号站台"),
        ),
    ),
    TravelDay(
        8,
        "交通（下）",
        "票价、充值、出口、方向：交通场景后半",
        (
            ("～までいくらですか", "~ made ikura desu ka", "到～多少钱"),
            ("チャージできますか", "chaaji dekimasu ka", "能充值吗（ICOCA）"),
            ("出口はどこですか", "deguchi wa doko desu ka", "出口在哪"),
            ("右 / 左 / まっすぐ", "migi / hidari / massugu", "右 / 左 / 直走"),
        ),
    ),
    TravelDay(
        9,
        "购物（上）",
        "试穿、换色、免税：购物场景前半",
        (
            ("これをください", "kore o kudasai", "要这个"),
            ("試着してもいいですか", "shichaku shite mo ii desu ka", "可以试穿吗"),
            ("別の色はありますか", "betsu no iro wa arimasu ka", "有别的颜色吗"),
            ("大きいサイズはありますか", "ookii saizu wa arimasu ka", "有大一号吗"),
            ("免税できますか", "menzei dekimasu ka", "可以免税吗"),
        ),
    ),
    TravelDay(
        10,
        "购物（下）",
        "付款方式、袋子、收据：购物场景后半",
        (
            ("クレジットカードで払えますか", "kurejitto kaado de haraemasu ka", "能刷卡吗"),
            ("現金で払います", "genkin de haraimasu", "用现金付"),
            ("袋はいりません", "fukuro wa irimasen", "不用袋子"),
            ("レシートをください", "reshiito o kudasai", "请给我收据"),
        ),
    ),
    TravelDay(
        11,
        "酒店（上）",
        "入住、退房、早餐：酒店场景前半",
        (
            ("チェックインお願いします", "chekku-in onegai shimasu", "办入住"),
            ("チェックアウトお願いします", "chekku-auto onegai shimasu", "退房"),
            ("予約しています", "yoyaku shiteimasu", "我有预约"),
            ("名前は～です", "namae wa ~ desu", "名字是～"),
            ("朝食は何時ですか", "choushoku wa nanji desu ka", "早餐几点"),
        ),
    ),
    TravelDay(
        12,
        "酒店（下）",
        "Wi-Fi、行李寄存、宅急便、毛巾：酒店场景后半",
        (
            ("Wi-Fiはありますか", "waifai wa arimasu ka", "有 Wi-Fi 吗"),
            ("パスワードは何ですか", "pasuwaado wa nan desu ka", "密码是多少"),
            ("荷物を預かってもらえますか", "nimotsu o azukatte moraemasu ka", "能寄存行李吗"),
            ("宅急便で送りたいです", "takkyubin de okuritai desu", "想用宅急便寄行李"),
            ("タオルをください", "taoru o kudasai", "请给我毛巾"),
        ),
    ),
    TravelDay(
        13,
        "餐厅综合对话模拟",
        "把餐厅句子串成一段完整点餐对话，练开口",
        (
            ("二人です", "futari desu", "两位"),
            ("メニューをください", "menyuu o kudasai", "请给我菜单"),
            ("おすすめは何ですか", "osusume wa nan desu ka", "有什么推荐"),
            ("これ、一つお願いします", "kore, hitotsu onegai shimasu", "这个来一份"),
            ("お会計お願いします", "o-kaikei onegai shimasu", "请结账"),
            ("ごちそうさまでした", "gochisousama deshita", "多谢款待"),
        ),
    ),
    TravelDay(
        14,
        "交通综合对话模拟",
        "问路、换乘、买票串成一段完整对话",
        (
            ("～へ行きたいです", "~ e ikitai desu", "我想去～"),
            ("この電車は～に行きますか", "kono densha wa ~ ni ikimasu ka", "这趟车去～吗"),
            ("乗り換えは必要ですか", "norikae wa hitsuyou desu ka", "需要换乘吗"),
            ("何番線ですか", "nanbansen desu ka", "在几号站台"),
            ("～までいくらですか", "~ made ikura desu ka", "到～多少钱"),
            ("出口はどこですか", "deguchi wa doko desu ka", "出口在哪"),
        ),
    ),
    TravelDay(
        15,
        "购物 + 酒店综合模拟",
        "购物、入住、寄存行李串成一段完整对话",
        (
            ("これをください", "kore o kudasai", "要这个"),
            ("試着してもいいですか", "shichaku shite mo ii desu ka", "可以试穿吗"),
            ("免税できますか", "menzei dekimasu ka", "可以免税吗"),
            ("チェックインお願いします", "chekku-in onegai shimasu", "办入住"),
            ("名前は～です", "namae wa ~ desu", "名字是～"),
            ("荷物を預かってもらえますか", "nimotsu o azukatte moraemasu ka", "能寄存行李吗"),
        ),
    ),
    TravelDay(
        16,
        "全场景串讲 + 听力冲刺",
        "价格报数 + 店员答复，把最常听到的话练熟",
        (
            ("いらっしゃいませ", "irasshaimase", "欢迎光临"),
            ("少々お待ちください", "shoushou omachi kudasai", "请稍等"),
            ("～円です", "~ en desu", "一共～日元"),
            ("かしこまりました", "kashikomarimashita", "好的 / 明白了"),
            ("こちらへどうぞ", "kochira e douzo", "这边请"),
            ("満席です", "manseki desu", "满座了"),
            ("売り切れです", "urikire desu", "卖完了"),
            ("現金のみです", "genkin nomi desu", "只收现金"),
            ("お待たせしました", "omatase shimashita", "让您久等了"),
            ("ありがとうございました", "arigatou gozaimashita", "谢谢（店员常说）"),
        ),
    ),
    TravelDay(
        17,
        "随身 cheat sheet + 出行 checklist",
        "最重要的救命句 + 打包清单，出行前一天过一遍",
        (
            ("すみません", "sumimasen", "不好意思 / 打扰一下"),
            ("これをください", "kore o kudasai", "请给我这个"),
            ("～はどこですか", "~ wa doko desu ka", "～在哪里"),
            ("いくらですか", "ikura desu ka", "多少钱"),
            ("英語を話せますか", "eigo o hanasemasu ka", "会说英语吗"),
            ("ゆっくり話してください", "yukkuri hanashite kudasai", "请说慢一点"),
            ("お会計お願いします", "o-kaikei onegai shimasu", "请结账"),
        ),
    ),
    TravelDay(
        18,
        "出行前轻复习",
        "全场景快速过一遍，出发当天轻松上阵",
        (
            ("二人です", "futari desu", "两位"),
            ("これをください", "kore o kudasai", "要这个"),
            ("～へ行きたいです", "~ e ikitai desu", "我想去～"),
            ("～はどこですか", "~ wa doko desu ka", "～在哪里"),
            ("免税できますか", "menzei dekimasu ka", "可以免税吗"),
            ("チェックインお願いします", "chekku-in onegai shimasu", "办入住"),
            ("ありがとうございました", "arigatou gozaimashita", "谢谢"),
        ),
    ),
)


def _parse_start(raw: str) -> date:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return date.fromisoformat("2026-11-10")


def day_for(cfg: Config) -> TravelDay:
    """Today's course day, cycling forever after the 18-day window."""
    start = _parse_start(cfg.travel_ja_start)
    today = datetime.now(ZoneInfo(cfg.timezone)).date()
    delta = (today - start).days
    index = delta % len(TRAVEL_DAYS) if delta >= 0 else 0
    return TRAVEL_DAYS[index]


def _phrase_lines(day: TravelDay) -> str:
    return "\n".join(
        f"- {japanese} | {romaji} | {chinese}" for japanese, romaji, chinese in day.phrases
    )


def build_travel_lesson(cfg: Config, day: TravelDay) -> dict[str, Any]:
    """Ask the LLM for one page of travel Japanese for this day, normalised."""
    user_content = (
        "请生成今天的旅行日语学习材料。\n\n"
        f"- 今天是第 {day.day} 天，主题：{day.title}\n"
        f"- 学习重点：{day.focus}\n"
        "- 我的水平：零基础，五十音会但拼读慢，完全不会汉字；目标是开口说 + "
        "听懂，请全程给假名和罗马字，尽量少用汉字。\n"
        "- 以下短语是今天的核心素材（日语 | 罗马字 | 中文），请优先使用它们，"
        "不要编造拿不准的日语：\n"
        f"{_phrase_lines(day)}\n\n"
        "请输出 JSON（结构见 system 里的 schema）。"
    )
    raw = complete_json(cfg, prompts.TRAVEL_JA_SYSTEM_PROMPT, user_content, what="travel lesson")
    return _normalize_travel(raw, day)


def _text(obj: Any, *keys: str) -> str:
    if not isinstance(obj, dict):
        return ""
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _items(raw: dict, key: str) -> list[dict]:
    value = raw.get(key)
    return [i for i in value if isinstance(i, dict)] if isinstance(value, list) else []


def _phrase(raw: dict) -> dict[str, str]:
    return {
        "japanese": _text(raw, "japanese", "text"),
        "reading": _text(raw, "reading", "kana"),
        "romaji": _text(raw, "romaji"),
        "chinese": _text(raw, "chinese", "meaning", "zh"),
    }


def _normalize_travel(raw: dict, day: TravelDay) -> dict[str, Any]:
    dialogue = []
    for line in _items(raw, "dialogue"):
        item = _phrase(line)
        if not item["japanese"]:
            continue
        item["speaker"] = _text(line, "speaker", "who") or "店员"
        dialogue.append(item)
    number_practice = []
    for line in _items(raw, "number_practice"):
        item = _phrase(line)
        if not item["japanese"]:
            continue
        item["answer"] = _text(line, "answer")
        number_practice.append(item)
    listening_quiz = [_phrase(line) for line in _items(raw, "listening_quiz")]
    listening_quiz = [i for i in listening_quiz if i["japanese"]]
    challenge = raw.get("reading_challenge")
    reading_challenge = _phrase(challenge) if isinstance(challenge, dict) else {}
    if isinstance(challenge, dict):
        reading_challenge["hint"] = _text(challenge, "hint")

    return {
        "day": day.day,
        "title": _text(raw, "title") or day.title,
        "intro": _text(raw, "intro"),
        "core_sentences": [
            i for i in (_phrase(line) for line in _items(raw, "core_sentences"))
            if i["japanese"]
        ][:5],
        "dialogue": dialogue[:6],
        "number_practice": number_practice[:3],
        "listening_quiz": listening_quiz[:1],
        "reading_challenge": reading_challenge,
    }


def build_travel_section(cfg: Config) -> render.TravelSection:
    """Build today's travel block (errors are caught by the caller)."""
    day = day_for(cfg)
    lesson_data = build_travel_lesson(cfg, day)
    return render.TravelSection(day=day.day, title=day.title, lesson=lesson_data)
