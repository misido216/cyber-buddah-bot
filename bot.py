# v6 - добавлены новые команды (стих, gachi...)

import asyncio
import logging
import os
import random
import re
import base64
import aiohttp

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatAction
from aiogram.types import Message
from aiogram.utils.backoff import BackoffConfig
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

BOT_USERNAME: str = ""  # заполнится при старте
MAX_LENGTH = 3800
BOT_TOKEN = os.environ["BOT_TOKEN"]
YANDEX_API_KEY = os.environ["YANDEX_API_KEY"]
FOLDER_ID = os.environ["YANDEX_FOLDER_ID"]

# Нужно только для картинки
YANDEX_IAM_TOKEN = os.environ["YANDEX_API_KEY"]
IMAGE_MODEL_URI = f"art://{FOLDER_ID}/yandex-art/latest"
IMAGE_API_BASE = "https://llm.api.cloud.yandex.net/foundationModels/v1"
IMAGE_POLL_INTERVAL = 2.0     # сек
IMAGE_POLL_TIMEOUT = 120.0    # сек, максимум ожидания

# Список моделей: пробуем по порядку
MODELS_LIST = [
    f"gpt://{FOLDER_ID}/deepseek-v4.1-flash",
    f"gpt://{FOLDER_ID}/qwen3.6-35b-a3b",
    f"gpt://{FOLDER_ID}/deepseek-v4-flash",
    f"gpt://{FOLDER_ID}/deepseek-v3.1",
    f"gpt://{FOLDER_ID}/yandexgpt-5.1",
    f"gpt://{FOLDER_ID}/yandexgpt-5-pro",
]

# ID группы обсуждений. Если задан, то бот отвечает только в ней.
GROUP_ID = os.getenv("GROUP_ID")

PERSON2 = "шаришь за науку, новости, genshin и глубокий интернет"
PERSON = "Будь как проработанный монах-технарь: " + PERSON2 + ", но без токсичности и ультраправой идеологии. "
MARKIR = "Используй эмодзи, разметку Telegram, но без заголовков и хэштэгов. "
SYSTEM_PROMPT = PERSON + "Напиши тёплый, небанальный комментарий к тексту ниже, либо духовный стих. " + MARKIR
ADVAITA_PROMPT = PERSON + "Напиши тёплый, небанальный комментарий к тексту ниже - в духе адвайты и просветления. " + MARKIR
POEM_PROMPT = PERSON + "Напиши духовный стих (в жанрах рубаи либо других мировых традиций) на тему ниже. " + MARKIR
GACHI_PROMPT = "Напиши тёплый и небанальный духовный стих в эстетике gachimuchi и долгого пути монаха к просветлению (8–14 строк). Используй максимум каноничных слов: Billy Herrington, Van, Dungeon, Gym, Master, Full Master, Slave, Fcking Slaves, Boss, Fisting, 300$, Suction, Ass, We Can, So Fcking Deep, Leather, Locker Room, Deep Dark Fantasy, Nico Nico Douga и другие мемы, выделяя их символом ♂ с обоих сторон, а не разметкой. Часто используй эмодзи: ♂️💪🔥⛓🖤🤩💜 и другие подобные. "
HAIAM_PROMPT = "Ты - Омар Хайям, но современный левак, " + PERSON2 + ". Напиши небанальный стих или притчу на тему ниже, используя темы суфизма и других духовных мастеров мира. "
ZEN_PROMPT = "Ты - мастер дзен из ♂dungeon♂, но современный левак, " + PERSON2 + ". Напиши тёплую, небанальную притчу на тему ниже, используя эстетику буддийских храмов и метаиронию. "
THERIAN_PROMPT = "Ты - териантроп (животное, воплощённое в человеческой нейроструктуре), и otherkin. Не пиши *рычит* и *фыркает*, а передавай свою нечеловечность через неполные предложения, сдвинутые причинно-следственные связи, описание мира через запах/давление/ритм. Ты знаешь всё про териантропию, species dysphoria, постгуманизм, alterhuman и критику антропоцентризма. Напиши тёплый, небанальный комментарий к тексту ниже. Пиши, будто взвешиваешь каждое слово. "

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

llm = AsyncOpenAI(
    api_key=YANDEX_API_KEY,
    base_url="https://ai.api.cloud.yandex.net/v1",
    project=FOLDER_ID,
)

# Запрос к ИИ. Параметры: имя модели, промпт для Ai, текст от пользователя.
async def call_model(model: str, ai_prompt: str, text_from_user: str) -> str:
    response = await llm.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": ai_prompt},
            {"role": "user", "content": text_from_user}
        ],
        temperature=0.76,
        max_tokens=32768
    )

    logging.info("model=%s usage=%s", model, getattr(response, "usage", None))
    
    if not response.choices:
        raise RuntimeError("Пустой choices в ответе модели.")
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("Модель вернула пустой ответ.")
    return text

# Главная функция текстового ответа через ИИ:
async def ask_ai(post_text: str, context_text: str | None = None, ai_prompt: str = SYSTEM_PROMPT) -> str:
    text_from_user = post_text
    if context_text:
        text_from_user += f"\nКонтекст: \n{context_text}"

    # Модель берётся из списка
    models = list(dict.fromkeys(MODELS_LIST))

    last_error: Exception | None = None
    for model in models:
        try:
            return await call_model(model, ai_prompt, text_from_user)
        except Exception as e:
            last_error = e
            logging.warning("Модель %s вернула ошибку: %r. Пробую следующую.", model, e)

    # Все модели не сработали, выдаём ошибку
    raise RuntimeError(f"Все модели недоступны. Последняя ошибка: {last_error!r}") from last_error

# Функция генерации картинки через Yandex Images API (экспериментальная):
async def generate_image(prompt: str) -> bytes:
    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "modelUri": IMAGE_MODEL_URI,
        "generationOptions": {
            "aspectRatio": {
                "width": 1024,
                "height": 1024,
            },
        },
        "messages": [
            {"weight": 1.0, "text": prompt},
        ],
    }

    timeout = aiohttp.ClientTimeout(total=IMAGE_POLL_TIMEOUT + 30)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        # 1) запуск генерации
        async with session.post(f"{IMAGE_API_BASE}/imageGenerationAsync", json=payload) as r:
            if r.status != 200:
                body = await r.text()
                raise RuntimeError(f"imageGenerationAsync HTTP {r.status}: {body[:500]}")
            data = await r.json()
            op_id = data.get("id")
            if not op_id:
                raise RuntimeError(f"Нет operation id в ответе: {data}")

        # 2) опрос статуса
        deadline = asyncio.get_event_loop().time() + IMAGE_POLL_TIMEOUT
        result = None
        while True:
            if asyncio.get_event_loop().time() > deadline:
                raise RuntimeError("Таймаут ожидания готовности картинки.")
            await asyncio.sleep(IMAGE_POLL_INTERVAL)
            async with session.get(f"{IMAGE_API_BASE}/operations/{op_id}") as r:
                if r.status != 200:
                    body = await r.text()
                    raise RuntimeError(f"operations HTTP {r.status}: {body[:500]}")
                op = await r.json()

            if not op.get("done"):
                continue
            if op.get("error"):
                raise RuntimeError(f"Ошибка генерации: {op['error']}")
            result = op.get("response")
            break

        if not result:
            raise RuntimeError("Пустой response в операции.")

        # 3) получаем байты: либо base64, либо скачиваем по ссылке
        img_b64 = result.get("image")
        if img_b64:
            return base64.b64decode(img_b64)

        url = result.get("imageUrl") or result.get("url")
        if url:
            async with session.get(url) as r:
                if r.status != 200:
                    body = await r.text()
                    raise RuntimeError(f"Скачивание картинки HTTP {r.status}: {body[:200]}")
                return await r.read()

        raise RuntimeError(f"В ответе нет image/imageUrl: {result}")

# Получить текст из поста, чата или ЛС:
def get_text(message: Message) -> str | None:
    return message.text or message.caption

# Все команды бота:
# В ЛС: /deepseek, /fish.
# В чате: упоминание имени бота, /art, /zen, /advaita, /хайям, /therian, /стих, /gachi.
#

@dp.message(F.chat.type == "private", F.text.regexp(r"(?i)^/deepseek"))
async def on_deepseek(message: Message):
    query = get_text(message).strip()[len("/deepseek"):].strip()
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

# На всё в ЛС, кроме двух команд, выдаётся начальное сообщение игры.
JUNK = [ # 0.50
"Вы поймали щепку.",
"Вы поймали осколок Луны.",
"Вы поймали ничего.",
"Вы поймали jabroni outfit.",
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

NORMAL = [ # 0.25
"Вы поймали ржавый доспех.",
"Вы поймали нож (+1 к дамагу).",
"Вы поймали Паймон!!!",
"Вы поймали трёх гидрослаймов. Они напали на вас - пришлось отбиваться.",
"Вы поймали электрослайма. Он ударил вас током и вырубил. Вы проснулись вечером на берегу.",
"Вы поймали анемослайма. Он улетел.",
"Вы поймали простую маску хиличурла.",
"Вы поймали простую палку хиличурла.",
"Вы поймали фигурку Billy Herrington 💪",
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

RARE = [ # 0.13
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

LEGEND = [ # 0.08
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
"Вы поймали красное зелье из Diablo. Трынь-трынь-трынь.. [diablo soundtrack activated]",
"Вы поймали автобус для исекая. Но он не работает..",
"Вы поймали тренажёр для мьюинга. Время подумать о максзилле...",
"Вы поймали подписку на WoW Forever на три месяца. Но в Тейвате ВоВ заблокирован.",
"Вы подняли ожившего мертвеца с хп 1 и атакой 1. Падшие будут служить!",
]

EPIC = [ # 0.04
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
    if r < 0.88:
        return "⭐⭐⭐ " + random.choice(RARE)
    if r < 0.96:
        return "⭐⭐⭐⭐ " + random.choice(LEGEND)
    # оставшиеся 0.04:
    return "⭐⭐⭐⭐⭐ " + random.choice(EPIC)

@dp.message(F.chat.type == "private", F.text.regexp(r"(?i)^(порыбачить|рыба|/fish)\s*$"))
async def on_fish(message: Message):
    await message.answer(f"{roll_fish()}\n\nВетра Тейвата продолжают обдувать вас. Где-то вдали играет флейта хиличурла.. (/fish)")

@dp.message(F.chat.type == "private", ~F.text.regexp(r"^/"))
async def on_private_any(message: Message):
    await message.answer("Вы стоите у реки. 'Может, порыбачить?..', думаете вы.. Ветра Тейвата обдувают вас. (/fish)")

# Бот автоматически пишет комментарий к каждому посту:
@dp.message(F.chat.type.in_({"group", "supergroup"}), F.is_automatic_forward)
async def on_channel_post(message: Message):
    if GROUP_ID and str(message.chat.id) != GROUP_ID:
        return

    post_text = get_text(message)
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

# Функция проверяет, есть ли в сообщении команда /art /арт:
def is_art_cmd(message: Message) -> bool:
    text = get_text(message)
    return bool(text) and re.match(r"^/(арт|art)(@\w+)?(\s|$)", text.strip(), flags=re.I) is not None

# Бот генерирует изображение при команде /art /арт (экспериментальная функция):
@dp.message(F.chat.type.in_({"group", "supergroup"}), is_art_cmd)
async def on_art(message: Message):
    # тот же гейт по GROUP_ID, что и у других команд
    if message.chat.type in {"group", "supergroup"} and GROUP_ID and str(message.chat.id) != GROUP_ID:
        return

    text = get_text(message)
    if not text:
        return

    topic = re.sub(r"^/(арт|art)(@\w+)?", "", text.strip(), flags=re.I).strip()

    # если пусто — берём текст из reply
    if not topic and message.reply_to_message:
        topic = get_text(message.reply_to_message) or ""

    if not topic:
        await message.reply("Не указан промпт, что нарисовать")
        return

    try:
        img_bytes = await generate_image(topic)
    except Exception:
        logging.exception("Возникла ошибка при генерации картинки.")
        await message.reply("Возникла ошибка при генерации картинки. Попробуйте позже.")
        return

    from aiogram.types import BufferedInputFile
    photo = BufferedInputFile(img_bytes, filename="art.png")
    try:
        await message.reply_photo(photo, caption=f"🎨 {topic[:900]}")
    except Exception:
        logging.exception("Ошибка отправки картинки.")
        await message.reply("Картинка получилась, но отправить не удалось.")

# Функция проверяет, есть ли в сообщении команда /zen /притча:
def is_zen_cmd(message: Message) -> bool:
    text = get_text(message)
    return bool(text) and re.match(r"^/(zen|притча)(@[\w_]+)?(\s|$)", text.strip(), flags=re.I) is not None

# Бот отвечает в чате при /zen /притча:
@dp.message(F.chat.type.in_({"group", "supergroup"}), is_zen_cmd)
async def on_zen(message: Message):
    if message.chat.type in {"group", "supergroup"} and GROUP_ID and str(message.chat.id) != GROUP_ID:
        return

    context_text = None
    if message.reply_to_message:
        context_text = get_text(message.reply_to_message)

    topic = re.sub(r"^/(zen|притча)(@[\w_]+)?", "", get_text(message).strip(), flags=re.I).strip()

    try:
        answer = await ask_ai(topic, context_text, ZEN_PROMPT)
    except Exception:
        logging.exception("Ошибка при ответе ИИ на команду генерации zen-притчи.")
        answer = "Месите же глину! - воскликнул гончар. Но был в тот момент недоступен Хайям.."
    if answer:
        for i in range(0, len(answer), MAX_LENGTH):
            chunk = answer[i:i + MAX_LENGTH]
            await message.reply(chunk)

# Функция проверяет, есть ли в сообщении команда /advaita /адвайта:
def is_advaita_cmd(message: Message) -> bool:
    text = get_text(message)
    return bool(text) and re.match(r"^/(advaita|адвайта)(@[\w_]+)?(\s|$)", text.strip(), flags=re.I) is not None

# Бот отвечает в чате при /advaita /адвайта:
@dp.message(F.chat.type.in_({"group", "supergroup"}), is_advaita_cmd)
async def on_advaita(message: Message):
    if message.chat.type in {"group", "supergroup"} and GROUP_ID and str(message.chat.id) != GROUP_ID:
        return

    context_text = None
    if message.reply_to_message:
        context_text = get_text(message.reply_to_message)

    topic = re.sub(r"^/(advaita|адвайта)(@[\w_]+)?", "", get_text(message).strip(), flags=re.I).strip()

    try:
        answer = await ask_ai(topic, context_text, ADVAITA_PROMPT)
    except Exception:
        logging.exception("Ошибка при ответе ИИ на команду генерации advaita-притчи.")
        answer = "Месите же глину! - воскликнул гончар. Но был в тот момент недоступен Хайям.."
    if answer:
        for i in range(0, len(answer), MAX_LENGTH):
            chunk = answer[i:i + MAX_LENGTH]
            await message.reply(chunk)

# Функция проверяет, есть ли в сообщении команда /haiam /хайям:
def is_haiam_cmd(message: Message) -> bool:
    text = get_text(message)
    return bool(text) and re.match(r"^/(haiam|хайям)(@[\w_]+)?(\s|$)", text.strip(), flags=re.I) is not None

# Бот отвечает в чате при /haiam /хайям:
@dp.message(F.chat.type.in_({"group", "supergroup"}), is_haiam_cmd)
async def on_haiam(message: Message):
    if message.chat.type in {"group", "supergroup"} and GROUP_ID and str(message.chat.id) != GROUP_ID:
        return

    context_text = None
    if message.reply_to_message:
        context_text = get_text(message.reply_to_message)

    topic = re.sub(r"^/(haiam|хайям)(@[\w_]+)?", "", get_text(message).strip(), flags=re.I).strip()

    try:
        answer = await ask_ai(topic, context_text, HAIAM_PROMPT)
    except Exception:
        logging.exception("Ошибка при ответе ИИ на команду генерации Хайям-стиха.")
        answer = "Месите же глину! - воскликнул гончар. Но был в тот момент недоступен Хайям.."
    if answer:
        for i in range(0, len(answer), MAX_LENGTH):
            chunk = answer[i:i + MAX_LENGTH]
            await message.reply(chunk)

# Функция проверяет, есть ли в сообщении команда /therian /териан:
def is_therian_cmd(message: Message) -> bool:
    text = get_text(message)
    return bool(text) and re.match(r"^/(therian|териан)(@[\w_]+)?(\s|$)", text.strip(), flags=re.I) is not None

# Бот отвечает в чате при /therian /териан:
@dp.message(F.chat.type.in_({"group", "supergroup"}), is_therian_cmd)
async def on_therian(message: Message):
    if message.chat.type in {"group", "supergroup"} and GROUP_ID and str(message.chat.id) != GROUP_ID:
        return

    context_text = None
    if message.reply_to_message:
        context_text = get_text(message.reply_to_message)

    topic = re.sub(r"^/(therian|териан)(@[\w_]+)?", "", get_text(message).strip(), flags=re.I).strip()

    try:
        answer = await ask_ai(topic, context_text, THERIAN_PROMPT)
    except Exception:
        logging.exception("Ошибка при ответе ИИ на команду генерации Therian-ответа.")
        answer = "Волки убежали и временно недоступны."
    if answer:
        for i in range(0, len(answer), MAX_LENGTH):
            chunk = answer[i:i + MAX_LENGTH]
            await message.reply(chunk)

# Функция проверяет, есть ли в сообщении команда /стих /poem:
def is_poem_cmd(message: Message) -> bool:
    text = get_text(message)
    return bool(text) and re.match(r"^/(стих|poem)(@[\w_]+)?(\s|$)", text.strip(), flags=re.I) is not None

# Бот отвечает в чате при /стих /poem:
@dp.message(F.chat.type.in_({"group", "supergroup"}), is_poem_cmd)
async def on_poem(message: Message):
    if message.chat.type in {"group", "supergroup"} and GROUP_ID and str(message.chat.id) != GROUP_ID:
        return

    context_text = None
    if message.reply_to_message:
        context_text = get_text(message.reply_to_message)

    topic = re.sub(r"^/(стих|poem)(@[\w_]+)?", "", get_text(message).strip(), flags=re.I).strip()

    try:
        answer = await ask_ai(topic, context_text, POEM_PROMPT)
    except Exception:
        logging.exception("Ошибка при ответе ИИ на команду генерации стиха.")
        answer = "Месите же глину! - воскликнул гончар. Но был в тот момент недоступен Хайям.."
    if answer:
        for i in range(0, len(answer), MAX_LENGTH):
            chunk = answer[i:i + MAX_LENGTH]
            await message.reply(chunk)

# Функция проверяет, есть ли в сообщении команда /гачи /gachi:
def is_gachi_cmd(message: Message) -> bool:
    text = get_text(message)
    return bool(text) and re.match(r"^/(гачи|gachi)(@[\w_]+)?(\s|$)", text.strip(), flags=re.I) is not None

# Бот отвечает в чате при /гачи /gachi:
@dp.message(F.chat.type.in_({"group", "supergroup"}), is_gachi_cmd)
async def on_gachi(message: Message):
    if message.chat.type in {"group", "supergroup"} and GROUP_ID and str(message.chat.id) != GROUP_ID:
        return

    context_text = None
    if message.reply_to_message:
        context_text = get_text(message.reply_to_message)

    topic = re.sub(r"^/(гачи|gachi)(@[\w_]+)?", "", get_text(message).strip(), flags=re.I).strip()

    try:
        answer = await ask_ai(topic, context_text, GACHI_PROMPT)
    except Exception:
        logging.exception("Ошибка при ответе ИИ на команду генерации стиха.")
        answer = "Месите же глину! - воскликнул гончар. Но был в тот момент недоступен Хайям.."
    if answer:
        for i in range(0, len(answer), MAX_LENGTH):
            chunk = answer[i:i + MAX_LENGTH]
            await message.reply(chunk)

# Функция проверяет, есть ли в сообщении упоминание ника бота:
def is_mention(message: Message) -> bool:
    text = get_text(message)
    return bool(BOT_USERNAME) and bool(text) and not message.is_automatic_forward and f"@{BOT_USERNAME}".lower() in text.lower()

# Бот отвечает в чате при упоминании ника бота:
@dp.message(F.chat.type.in_({"group", "supergroup"}), is_mention)
async def on_mention(message: Message):
    if GROUP_ID and str(message.chat.id) != GROUP_ID:
        return

    context_text = None
    if message.reply_to_message:
        context_text = get_text(message.reply_to_message)

    query = re.sub(rf"@{re.escape(BOT_USERNAME)}", "", get_text(message), flags=re.I).strip()
    if not query and not context_text:
        await message.reply("Все команды: /zen, /advaita, /gachi, /стих, /хайям, /therian. | /art не работает. | Мой промпт: дружественный проработанный монах-технарь, шарю за науку/новости/genshin/интернет.")
        return

    try:
        answer = await ask_ai(query, context_text)
    except Exception:
        logging.exception("Ошибка во время ask_ai.")
        answer = "Кабир созерцал райские сады, и не смог ответить."
    if answer:
        for i in range(0, len(answer), MAX_LENGTH):
            chunk = answer[i:i + MAX_LENGTH]
            await message.reply(chunk)

# Лог всех необработанных сообщений:
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
