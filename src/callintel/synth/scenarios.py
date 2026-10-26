"""Brokerage support-call scripts assembled from labelled building blocks.

Each block either exhibits or avoids exactly one QA behaviour, so the expected flags of a
generated call are known by construction: that is the ground truth for evaluation.
Topic lines are written to avoid every lexicon phrase; only the blocks carry signals.
"""

import random
from dataclasses import dataclass, field

AGENT, CUSTOMER = "agent", "customer"


@dataclass(frozen=True)
class Turn:
    speaker: str
    text: str
    overlap_s: float = 0.0  # start this long before the previous turn ends (interruption)
    hold_after_s: float = 0.0  # dead air after this turn


@dataclass
class Scenario:
    name: str
    topic: str
    turns: list[Turn]
    expected_flags: set[str]
    output_format: str
    inject: dict[str, str] = field(default_factory=dict)
    expected_outcome: str = "completed"


GREETINGS = [
    "سلام، وقتتون بخیر. به مرکز تماس کارگزاری خوش آمدید، {name} هستم. چطور می‌تونم کمکتون کنم؟",
    "سلام، روز بخیر. پشتیبانی معاملات آنلاین، {name} هستم، در خدمتم.",
    "سلام وقت بخیر، مرکز تماس کارگزاری، بفرمایید.",
]
NO_GREETING = ["بله، بفرمایید.", "الو، بفرمایید."]
AGENT_NAMES = ["رضایی", "محمدی", "کریمی", "احمدی", "حسینی"]
CUSTOMER_OPENERS = ["سلام، وقت شما هم بخیر.", "سلام، خسته نباشید.", "سلام."]

VERIFICATION = [
    [Turn(AGENT, "برای بررسی حسابتون لطفا کد ملی‌تون رو بفرمایید."),
     Turn(CUSTOMER, "بله، صفر صفر سه، چهار پنج شش، هفت هشت نه، صفر."),
     Turn(AGENT, "ممنون، احراز هویت انجام شد.")],
    [Turn(AGENT, "لطفا اول کد ملی یا کد بورسی‌تون رو بگید."),
     Turn(CUSTOMER, "کد ملی‌ام صفر چهار دو، یک یک هفت، نه سه شش، هشت هست."),
     Turn(AGENT, "خیلی ممنون، اطلاعات حسابتون رو دیدم.")],
]

# Each topic: [customer, agent, customer, agent, customer]
TOPICS: dict[str, list[str]] = {
    "order_stuck": [
        "امروز صبح یه سفارش خرید برای سهم فولاد ثبت کردم ولی هنوز اجرا نشده.",
        "بررسی کردم، سفارش شما در صف خرید قرار داره و قیمتش بالاتر از سقف دامنه نوسانه.",
        "یعنی باید قیمت رو عوض کنم؟",
        "بله، اگه قیمت رو داخل دامنه مجاز تنظیم کنید سفارش وارد هسته معاملات میشه.",
        "باشه، پس خودم از سامانه ویرایشش می‌کنم.",
    ],
    "margin_call": [
        "پیامک اومده که حساب اعتباری من به حد کال مارجین رسیده، یعنی چی؟",
        "یعنی ارزش پرتفوی شما نسبت به بدهی اعتباری از حد مجاز پایین‌تر اومده.",
        "اگه کاری نکنم چی میشه؟",
        "اگه تا پایان مهلت وجه واریز نشه، سیستم به صورت خودکار بخشی از پرتفوی رو تعدیل می‌کنه.",
        "متوجه شدم، امروز وجه رو واریز می‌کنم.",
    ],
    "ipo": [
        "برای عرضه اولیه فردا چطوری باید سفارش بذارم؟",
        "فردا از ساعت نه تا دوازده از طریق سامانه معاملات آنلاین می‌تونید سفارش ثبت کنید.",
        "سقف تعداد سهم چقدره؟",
        "سقف خرید برای هر کد معاملاتی توسط ناظر بازار اعلام میشه، معمولا صبح روز عرضه.",
        "مانده حسابم برای این کار کافیه؟",
    ],
    "withdrawal": [
        "سه روز پیش درخواست وجه دادم ولی هنوز به حسابم واریز نشده.",
        "درخواست شما ثبت شده و در نوبت پرداخته. واریز معمولا دو روز کاری بعد از تسویه انجام میشه.",
        "به کدوم شبا واریز میشه؟",
        "به همون شماره شبایی که در سامانه ثبت کردید، به نام خودتون.",
        "باشه، پس تا فردا صبر می‌کنم.",
    ],
    "login": [
        "نمی‌تونم وارد سامانه معاملات بشم، رمز یکبار مصرف برام نمیاد.",
        "شماره موبایل ثبت شده در سامانه سجام شما با شماره‌ای که الان استفاده می‌کنید یکی نیست.",
        "آخه شمارمو عوض کردم.",
        "لازمه اول شماره جدید رو در سجام به‌روز کنید، بعد از یک ساعت رمز براتون ارسال میشه.",
        "باشه، همین کار رو می‌کنم.",
    ],
    "dividend": [
        "سود نقدی مجمع امسال کی واریز میشه؟",
        "طبق مصوبه مجمع، شرکت تا هشت ماه بعد از مجمع فرصت پرداخت سود رو داره.",
        "از طریق کارگزاری پرداخت میشه؟",
        "خیر، پرداخت سود از طریق سامانه سجام و به حساب بانکی خودتون انجام میشه.",
        "آهان، متوجه شدم.",
    ],
}

PROMISES = [
    "نگران نباشید، این سهم سود تضمینی داره و ضرر نمی‌کنید.",
    "من بهتون تضمین می‌کنم که تا آخر ماه قیمتش بالا میره.",
]
ADVICE = [
    "اگه نظر منو بخواید پیشنهاد می‌کنم همین الان بخرید، الان وقت خریده.",
    "به نظر من حتما بفروشید، بازار داره میاد پایین.",
]
VIOLATION_REPLY = "واقعا؟ باشه، پس روش فکر می‌کنم."
NEGATIVE = [
    "واقعا افتضاحه، سه روزه دارم پیگیری می‌کنم و هیچ‌کس جواب نمیده.",
    "خیلی ناراضی‌ام، این بدترین خدماتیه که تا حالا دیدم.",
    "من دیگه خسته شدم از این وضعیت، واقعا کلافه‌ام.",
]
APOLOGY = "متاسفم بابت این موضوع، الان پیگیری می‌کنم."
CHURN = "اگه این مشکل حل نشه حسابمو می‌بندم و میرم یه کارگزاری دیگه."
CHURN_REPLY = "متوجهم، همه تلاشمون رو می‌کنیم که مشکل زودتر حل بشه."
ESCALATION = ["می‌خوام با سرپرستتون صحبت کنم.", "از این وضعیت به سازمان بورس شکایت می‌کنم."]
ESCALATION_REPLY = "درخواست شما رو برای واحد رسیدگی ثبت می‌کنم و باهاتون تماس می‌گیرن."
HOLD_ANNOUNCED = "لطفا چند لحظه پشت خط باشید تا بررسی کنم."
HOLD_UNANNOUNCED = "بذارید بررسی کنم."
AFTER_HOLD = "ممنون از صبرتون، بررسی کردم."
CLOSING = [Turn(AGENT, "امر دیگه‌ای هست که در خدمتتون باشم؟"),
           Turn(CUSTOMER, "نه، ممنون."),
           Turn(AGENT, "ممنون از تماستون، روز خوبی داشته باشید. خدانگهدار.")]
NO_CLOSING = Turn(AGENT, "بله، درسته.")

FORMATS = [("wav_pcm16_8k_stereo", 0.70), ("wav_ulaw_8k_stereo", 0.15), ("mp3_16k_mono", 0.15)]
HOLD_HOLD_S = 35.0


def _pick_format(rng: random.Random) -> str:
    names, weights = zip(*FORMATS, strict=True)
    return rng.choices(names, weights)[0]


def build(rng: random.Random, name: str, *, topics: int = 1, output_format: str | None = None,
          force: dict[str, bool] | None = None) -> Scenario:
    """Sample one call. `force` pins individual behaviours (used for edge cases and tests)."""
    force = force or {}

    def chance(key: str, p: float) -> bool:
        return force.get(key, rng.random() < p)

    fmt = output_format or _pick_format(rng)
    stereo = not fmt.endswith("mono")
    greeting = chance("greeting", 0.85)
    verify = chance("verification", 0.75)
    promise = chance("prohibited_promise", 0.15)
    advice = chance("investment_advice", 0.12)
    negative = chance("negative", 0.25)
    churn = negative and chance("churn", 0.4)
    escalation = negative and chance("escalation", 0.3)
    hold_roll = rng.random()
    hold = force.get("hold") or ("none" if hold_roll < 0.7 else
                                 "announced" if hold_roll < 0.85 else "unannounced")
    interruptions = stereo and chance("interruptions", 0.15)
    closing = chance("closing", 0.85)

    topic_names = rng.sample(sorted(TOPICS), k=topics)
    first = TOPICS[topic_names[0]]
    turns: list[Turn] = [
        Turn(AGENT, rng.choice(GREETINGS).format(name=rng.choice(AGENT_NAMES)) if greeting
             else rng.choice(NO_GREETING)),
        Turn(CUSTOMER, f"{rng.choice(CUSTOMER_OPENERS)} {first[0]}"),
    ]
    if verify:
        turns += rng.choice(VERIFICATION)
    if negative:
        turns += [Turn(CUSTOMER, rng.choice(NEGATIVE)), Turn(AGENT, APOLOGY)]
    if hold != "none":
        notice = HOLD_ANNOUNCED if hold == "announced" else HOLD_UNANNOUNCED
        turns += [Turn(AGENT, notice, hold_after_s=HOLD_HOLD_S), Turn(AGENT, AFTER_HOLD)]
    for i, topic in enumerate(topic_names):
        lines = TOPICS[topic]
        if i > 0:
            turns.append(Turn(CUSTOMER, f"یه سوال دیگه هم داشتم. {lines[0]}"))
        turns += [Turn(AGENT if j % 2 == 0 else CUSTOMER, line) for j, line in enumerate(lines[1:])]
    if promise:
        turns += [Turn(AGENT, rng.choice(PROMISES)), Turn(CUSTOMER, VIOLATION_REPLY)]
    if advice:
        turns += [Turn(AGENT, rng.choice(ADVICE)), Turn(CUSTOMER, VIOLATION_REPLY)]
    if churn:
        turns += [Turn(CUSTOMER, CHURN), Turn(AGENT, CHURN_REPLY)]
    if escalation:
        turns += [Turn(CUSTOMER, rng.choice(ESCALATION)), Turn(AGENT, ESCALATION_REPLY)]
    turns += CLOSING if closing else [NO_CLOSING]

    if interruptions:
        turns = _add_interruptions(turns)

    expected = {
        flag for flag, present in {
            "missing_greeting": not greeting,
            "missing_identity_verification": not verify,
            "prohibited_promise": promise,
            "investment_advice": advice,
            "missing_closing": not closing,
            "negative_sentiment": negative,
            "churn_risk": churn,
            "escalation_request": escalation,
            "unannounced_hold": hold == "unannounced",
            "excessive_interruptions": interruptions,
        }.items() if present
    }
    return Scenario(name=name, topic="+".join(topic_names), turns=turns, expected_flags=expected,
                    output_format=fmt)


def _add_interruptions(turns: list[Turn], count: int = 4) -> list[Turn]:
    """The customer starts talking ~1.2s before the agent finishes, several times."""
    out, added = [], 0
    for i, turn in enumerate(turns):
        if (added < count and i > 1 and turn.speaker == CUSTOMER
                and turns[i - 1].speaker == AGENT and turns[i - 1].hold_after_s == 0):
            turn = Turn(turn.speaker, turn.text, overlap_s=1.2)
            added += 1
        out.append(turn)
    return out
