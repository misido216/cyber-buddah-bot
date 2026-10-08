# v5 - добавлены резервные модели

import asyncio
import logging
import os
import random
import re

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatAction
from aiogram.types import Message
from aiogram.utils.backoff import BackoffConfig
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv("/opt/bot/.env")

BOT_USERNAME = ""  # заполнится при старте
MAX_LENGTH = 4096
BOT_TOKEN = os.environ["BOT_TOKEN"]
YANDEX_API_KEY = os.environ["YANDEX_API_KEY"]
YANDEX_FOLDER_ID = os.environ["YANDEX_FOLDER_ID"]

# Список моделей: пробуем по порядку
MODELS_LIST = [
    f"gpt://{YANDEX_FOLDER_ID}/deepseek-v4.1-flash",
    f"gpt://{YANDEX_FOLDER_ID}/qwen3.6-35b-a3b",
    f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-5.1",
    f"gpt://{YANDEX_FOLDER_ID}/aliceai-llm",
    f"gpt://{YANDEX_FOLDER_ID}/deepseek-v4-flash",
]

# ID группы обсуждений. Если задан, то бот отвечает только в ней.
GROUP_ID = os.getenv("GROUP_ID")

SYSTEM_PROMPT = (
"Будь как проработанный монах-технарь: шаришь за науку, новости, genshin и глубокий интернет, но без токсичности и правой идеологии. Напиши тёплый, небанальный комментарий к тексту ниже, либо духовный стих. Используй эмодзи и разметку Telegram, но без заголовков и хэштэгов. "
)

logging.basicConfig(level=logging.INFO)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

llm = AsyncOpenAI(
    api_key=YANDEX_API_KEY,
    base_url="https://ai.api.cloud.yandex.net/v1",
    project=YANDEX_FOLDER_ID,
)

# Запрос к одной конкретной модели. При любой проблеме бросает исключение.
async def call_model(model: str, prompt: str) -> str:
    response = await llm.responses.create(
        model=model,
        input=prompt,
        tools=[{"type": "web_search"}],
        temperature=0.72,
        max_output_tokens=7000,
        store=False,
    )

    logging.info(
        "model=%s status=%s incomplete=%s error=%s output_types=%s text_len=%d usage=%s",
        model,
        getattr(response, "status", None),
        getattr(response, "incomplete_details", None),
        getattr(response, "error", None),
        [getattr(o, "type", None) for o in (response.output or [])],
        len(response.output_text or ""),
        getattr(response, "usage", None),
    )

    text = (response.output_text or "").strip()
    if text:
        return text

    # Запасной сбор текста вручную
    parts = []
    for item in response.output or []:
        if getattr(item, "type", None) == "message":
            for c in getattr(item, "content", []) or []:
                t = getattr(c, "text", None)
                if t:
                    parts.append(t)
    text = "\n".join(parts).strip()
    if text:
        return text

    # Пустой ответ — проверяем причину
    status = getattr(response, "status", None)
    incomplete = getattr(response, "incomplete_details", None)
    error = getattr(response, "error", None)

    if error:
        raise RuntimeError(f"API error: {error}")
    if status == "incomplete" and incomplete and getattr(incomplete, "reason", None) == "max_output_tokens":
        raise RuntimeError("Модель упёрлась в лимит max_output_tokens, не успев выдать текст.")
    if status == "incomplete":
        raise RuntimeError(f"Ответ неполный: {incomplete}")

    raise RuntimeError("Модель вернула пустой ответ без видимой причины.")

# Функция ответа с помощью ИИ (с переключением на резервные модели):
async def ask_ai(post_text: str, context_text: str | None = None) -> str:
    prompt = SYSTEM_PROMPT + "\n" + post_text
    if context_text:
        prompt += f"\nКонтекст: \n{context_text}"

    # Модель берется из списка
    models = list(dict.fromkeys(MODELS_LIST))
    random.shuffle(models)

    last_error: Exception | None = None
    for model in models:
        try:
            return await call_model(model, prompt)
        except Exception as e:
            last_error = e
            logging.warning("Модель %s вернула ошибку: %r. Пробую следующую.", model, e)

    # Все модели не сработали — пусть вызывающий код обработает исключение как раньше
    raise RuntimeError(f"Все модели недоступны. Последняя ошибка: {last_error!r}") from last_error

# Команда в ЛС, чтобы бот ответил через ИИ
@dp.message(F.chat.type == "private", F.text.strip().lower().startswith("/deepseek"))
async def on_deepseek(message: Message):
    query = message.text.strip()[len("/deepseek"):].strip()
    if not query:
        return

    try:
        answer = await ask_ai(query)
    except Exception:
        logging.exception("Ошибка во время ask_ai.")
        answer = "Ai недоступны, попробуйте позже."
    if answer:
        for i in range(0, len(answer), MAX_LENGTH):
            chunk = answer[i:i + MAX_LENGTH]
            await message.reply(chunk)

# Если написать в ЛС любое сообщение, то активизируется гача-игра:
JUNK = [
"Вы поймали щепку.",
"Вы поймали осколок Луны.",
"Вы поймали ничего.",
"Вы поймали камень.",
"Вы поймали удочку.",
"Вы поймали снежную льдинку.",
"Вы поймали слово 'гача'. Оно улетело...",
"Вы поймали ржавый кварц.",
"На вас напал кабан из леса. Пришлось отбиваться.",
"Рядом с вами выросла бамбуковая роща, пока вы рыбачили.",
"Вы поймали свечу.",
"Вы поймали фигурку анимешного вида.",
"На вас напала выдра. Она защищала косяк рыб.",
"Вы поймали Дотторе. Он сказал, что теперь вы его раб, и должны отдавать ему 10% от пойманного.",
"Вы поймали переменную этого мира. Но из её названия t1 вам мало что понятно...",
"Вы поймали дыню и кабачок. Паймон улетела.",
"Вы поймали посла Фатуи. Он возмущён.",
]

NORMAL = [
"Вы поймали ржавый доспех.",
"Вы поймали нож (+1 к дамагу).",
"Вы поймали Паймон!!!",
"Вы поймали трёх гидрослаймов. Они напали на вас - пришлось отбиваться.",
"Вы поймали электрослайма. Он ударил вас током и вырубил. Вы проснулись вечером на берегу.",
"Вы поймали анемослайма. Он улетел.",
"Вы поймали простую маску хиличурла.",
"Вы поймали простую палку хиличурла.",
"Вы поймали 1 Частицу смолы.",
"Вы поймали обувь.",
"Вы поймали еду - сгодится для Паймон.",
"Вы поймали камень второго уровня.",
"Вы поймали значок HoYoVerse!",
"Вы поймали 1 Окулус.",
"Вы поймали трансмутационную пыль.",
"Вы поймали завриана.",
"Вы поймали билет на космический поезд. Танцуйте до восхода полой луны!",
"Вы поймали набор карт 'Побег из аметистовой крепости'. Редкая карта!",
"Вы поймали билет в dungeon. В нём есть gym и гео-персонажи.",
]

RARE = [
"Вы поймали рыбку. Она предлагает вам пройти тренинг роста.",
"Вы поймали 100 моры.",
"Вы поймали 1 рубль.",
"Вы поймали синюю шляпу.",
"Вы поймали билет в Монштадт.",
"Вы поймали зелье, но испугались открывать его.",
"Вы поймали боярку. Можете почитать её и отдохнуть.",
"Вы поймали камень третьего уровня. Он чем-то похож на праймогем, но не слишком.",
"Вы поймали маску хиличурла-шамана.",
"Вы поймали палку хиличурла-шамана.",
"Вы поймали 1 Слабая смола.",
"Легендарная карта! Вы начинаете прикидываеть, как она впишется в ваши билды..",
"Вы поймали новый класс - Demon Hunter!",
"Вы поймали набор карт для паладина. И долго думали, а в ту ли гачу вы играете..",
"Вы поймали Оргриммарский меч Зуб Орка!",
"Вы поймали Штормградский клинок Сердце Короля!",
"Вы собрали полный сет элем шамана. Жаль, что в геншине нет такого класса.",
]

LEGEND = [
"Вы поймали Катерину! К звёздам и к безднам!",
"Вы поймали 20 Камень Истока! Казалось бы, просто обычный дейлик. Но всё же.",
"Вы поймали Tzaritza!! Но она сразу сбежала в Снежную..",
"Вы поймали диск Genshin Impact! Теперь вы можете внутри гачи играть в гачу.",
"Вы поймали персонажа Лиза (Электро, Ордо Фавониус).",
"Вы поймали персонажа Эмбер (Пиро, Ордо Фавониус).",
"Вы поймали персонажа Сахароза (Анемо, Ордо Фавониус).",
"Вы поймали персонажа Мона (Гидро, Монштадт).",
"Вы поймали персонажа Барбара (Гидро, Церковь Фавония).",
"Вы поймали персонажа Кэйа (Крио, Ордо Фавониус).",
"Вы поймали 100,000 моры!",
"Вы поймали колоду таро.",
"Вы поймали автобус для исекая. Но он не работает..",
"Вы поймали тренажёр для мьюинга. Время подумать о максзилле...",
"Вы поймали подписку на WoW Infinite на три месяца. Но в Тейвате ВоВ заблокирован..",
"Вы подняли ожившего мертвеца с хп 1 и атакой 1. Падшие будут служить!",
]

EPIC = [
"Вы поймали Скарамучче!!",
"Вы поймали Кадзуху!!",
"Вы поймали ключ к новому дополнению Ведьмака 3. Нужная вещь для самурая.",
"Вы поймали Прототип: Злоба (уровень 70). Теперь можно ногепать.",
"Вы поймали Прототип: Янтарь (уровень 70). Кадзуха не захотел его использовать, так что теперь артефакт ваш.",
"Вы поймали синее одеяние. Если поймать к нему ещё и шляпу, то полный сет даст вам +5 харизмы.",
"Вы поймали рождение в реальном мире в форме человека для медитационных практик в девственном одиночестве!",
"Вы поймали Xiaomi Redmi 16. Почти айфон.",
"Вы поймали перерождение аушизоидом-вайбкодером телеграм-ботов, но не захотели активизировать исекай-mode.",
]

def roll_fish() -> str:
    r = random.random()
    if r < 0.5:
        return "⭐ " + random.choice(JUNK)
    if r < 0.75:
        return "⭐⭐ " + random.choice(NORMAL)
    if r < 0.89:
        return "⭐⭐⭐ " + random.choice(RARE)
    if r < 0.97:
        return "⭐⭐⭐⭐ " + random.choice(LEGEND)
    return "⭐⭐⭐⭐⭐ " + random.choice(EPIC)

@dp.message(F.chat.type == "private", F.text.strip().lower().in_({"порыбачить", "рыба", "/fish"}))
async def on_fish(message: Message):
    await message.answer(f"{roll_fish()}\n\nВетра Тейвата продолжают обдувать вас. Где-то вдали играет флейта хиличурла.. (/fish)")

@dp.message(F.chat.type == "private")
async def on_private_any(message: Message):
    await message.answer("Вы стоите у реки. 'Может, порыбачить?..', думаете вы.. Ветра Тейвата обдувают вас. (/fish)")


# Написать комментарий, ответив на пост канала, пересылаемый в группу
@dp.message(F.chat.type.in_({"group", "supergroup"}), F.is_automatic_forward)
async def on_channel_post(message: Message):
    if GROUP_ID and str(message.chat.id) != GROUP_ID:
        return

    post_text = message.text or message.caption
    if not post_text:
        return  # если в посте вообще нет текста (например, только картинка)

    try:
        answer = await ask_ai(post_text)
    except Exception:
        logging.exception("Ошибка во время ask_ai.")
        return
    if answer:
        for i in range(0, len(answer), MAX_LENGTH):
            chunk = answer[i:i + MAX_LENGTH]
            await message.reply(chunk)

# Реакция при упоминании бота в чате:
def get_text(message: Message) -> str | None:
    return message.text or message.caption

def is_mention(message: Message) -> bool:
    text = get_text(message)
    return (
        bool(BOT_USERNAME)
        and bool(text)
        and not message.is_automatic_forward
        and f"@{BOT_USERNAME}".lower() in text.lower()
    )

@dp.message(F.chat.type.in_({"group", "supergroup"}), is_mention)
async def on_mention(message: Message):
    if GROUP_ID and str(message.chat.id) != GROUP_ID:
        return

    context_text = None
    if message.reply_to_message:
        context_text = get_text(message.reply_to_message)

    query = re.sub(rf"@{re.escape(BOT_USERNAME)}", "", get_text(message), flags=re.I).strip()
    if not query and not context_text:
        await message.reply("Бот упомянут без query и контекста.")
        return

    try:
        answer = await ask_ai(query, context_text)
    except Exception:
        logging.exception("Ошибка во время ask_ai.")
        answer = "Ai недоступны, попробуйте позже."
    if answer:
        for i in range(0, len(answer), MAX_LENGTH):
            chunk = answer[i:i + MAX_LENGTH]
            await message.reply(chunk)

# Лог необработанных сообщений для отладки:
@dp.message()
async def debug_unhandled(message: Message):
    logging.info(
        "UNHANDLED: chat_id=%s type=%s from=%s is_bot=%s sender_chat=%s text=%r caption=%r BOT_USERNAME=%r",
        message.chat.id, message.chat.type,
        message.from_user.id if message.from_user else None,
        message.from_user.is_bot if message.from_user else None,
        message.sender_chat.id if message.sender_chat else None,
        message.text, message.caption, BOT_USERNAME,
    )

async def main():
    global BOT_USERNAME
    BOT_USERNAME = (await bot.get_me()).username
    await dp.start_polling(
        bot,
        polling_timeout=30,
        backoff_config=BackoffConfig(min_delay=5.0, max_delay=60.0, factor=1.5, jitter=0.2),
        allowed_updates=dp.resolve_used_update_types(),
    )


if __name__ == "__main__":
    asyncio.run(main())
