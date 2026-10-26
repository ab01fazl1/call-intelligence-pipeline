"""Persian phrase lists used by the QA rules.

Kept as data, separate from rule logic, so compliance staff could own them. Spacing and ZWNJ
variants do not need to be listed separately; see text.normalize.Lexicon.
"""

from callintel.text.normalize import Lexicon

GREETING = Lexicon("greeting", (
    "سلام", "وقت بخیر", "وقتتون بخیر", "روز بخیر", "روزتون بخیر", "عصر بخیر",
    "خوش آمدید", "خوش اومدید",
))

IDENTITY_CHECK = Lexicon("identity_check", (
    "کد ملی", "کدملی", "کد بورسی", "شماره مشتری", "احراز هویت", "تاریخ تولد",
))

# Promising returns is prohibited for brokerage staff (market-conduct regulation).
PROHIBITED_PROMISE = Lexicon("prohibited_promise", (
    "سود تضمینی", "تضمین می کنم", "تضمین میدم", "ضرر نمی کنید", "ضرر نمیکنید",
    "بدون ریسک", "صد در صد سود", "حتما سود می کنید", "حتما سود میکنید",
))

# Support agents must not give personalised buy/sell recommendations.
INVESTMENT_ADVICE = Lexicon("investment_advice", (
    "پیشنهاد می کنم بخرید", "پیشنهاد می کنم بفروشید", "حتما بخرید", "حتما بفروشید",
    "همین الان بخرید", "همین الان بفروشید", "وقت خریده", "وقت فروشه", "جای شما بودم می خریدم",
))

CLOSING = Lexicon("closing", (
    "روز خوبی داشته باشید", "روز خوش", "خدانگهدار", "خدا نگهدار", "امر دیگه ای",
    "امر دیگری", "در خدمتتون باشم", "ممنون از تماستون", "ممنون از تماس شما",
))

HOLD_NOTICE = Lexicon("hold_notice", (
    "چند لحظه", "پشت خط", "منتظر بمونید", "منتظر بمانید", "گوشی خدمتتون", "یه لحظه",
))

NEGATIVE = Lexicon("negative", (
    "افتضاح*", "ناراضی*", "عصبانی*", "بدترین", "خسته شدم", "مسخره*", "بی مسئولیت",
    "هیچ کس جواب نمیده", "اعصابم", "شرم آور", "کلافه*", "بی نظم*",
))

POSITIVE = Lexicon("positive", (
    "ممنون*", "متشکرم", "مرسی", "لطف کردید", "عالی", "راضی*", "دستتون درد نکنه",
))

CHURN = Lexicon("churn", (
    "حسابمو می بندم", "حسابم رو می بندم", "حسابم را می بندم", "کارگزاری دیگه",
    "کارگزاری دیگر", "تغییر کارگزار", "انتقال سهام", "دیگه با شما کار نمی کنم",
))

ESCALATION = Lexicon("escalation", (
    "سرپرست*", "مدیرتون", "مدیر بخش", "شکایت*", "سازمان بورس",
))
