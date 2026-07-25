from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Iterator, Optional

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SetBusinessAccountName
from aiogram.types import (
    BusinessConnection,
    CallbackQuery,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

logger = logging.getLogger("timenick")

# ======================================================================
# CONFIG
# ======================================================================

# Каждый custom_emoji_id в Telegram привязан к конкретному стандартному
# эмодзи-носителю (тому, поверх которого рисуется премиум-версия). Поэтому
# храним ID вместе с его настоящим fallback-символом, а не произвольным.
# Фраза, которая должна стоять в описании профиля (Bio) пользователя, чтобы
# у него была активна бесплатная подписка "за био". Пропадает из описания —
# пропадает и подписка (проверяется при каждом цикле обновления ника).
BIO_SUB_PHRASE = "@see_time_bot"

# Сколько дней подписки начисляется рефереру за одного реально подключённого
# (через Business Connection) приглашённого.
REFERRAL_BONUS_DAYS = 2

DEFAULT_EMOJI: dict[str, tuple[str, str]] = {
    "welcome_check": ("5463161330649298358", "✅"),
    "opt_time": ("5391026156617115607", "1️⃣"),
    "opt_date": ("5391147369184141550", "2️⃣"),
    "opt_countdown": ("5390812022432638736", "3️⃣"),
    "star": ("5952066863931331270", "⭐"),
}

# Пак готовых премиум-эмодзи (398 шт., загружены пользователем).
# Засеивается в БД при init_schema так же, как DEFAULT_EMOJI — все ключи
# сразу доступны для вставки в текст через {emoji:ключ}.
EMOJI_PACK: list[tuple[str, str, str]] = [
    # (key, fallback_symbol, custom_emoji_id)
    ("pack_001", "✈️", "6028346797368283073"),
    ("pack_002", "🍏", "5775870512127283512"),
    ("pack_003", "⭐️", "6028338546736107668"),
    ("pack_004", "⭐️", "5767199127775481841"),
    ("pack_005", "🎁", "5773677501825945508"),
    ("pack_006", "🔣", "6035162669948867129"),
    ("pack_007", "🖥", "5942734685976138521"),
    ("pack_008", "⚙️", "5904258298764334001"),
    ("pack_009", "⚙", "6032742198179532882"),
    ("pack_010", "⚙️", "5850309953293653168"),
    ("pack_011", "⚙️", "5850332476102153487"),
    ("pack_012", "⚙️", "5850392884817172292"),
    ("pack_013", "⚙️", "5850224242926295392"),
    ("pack_014", "⚙️", "5924722061288150929"),
    ("pack_015", "🎛", "5776424837786374634"),
    ("pack_016", "🎛", "5771449289972650710"),
    ("pack_017", "📰", "5895519358871932592"),
    ("pack_018", "📰", "5893444447286334441"),
    ("pack_019", "📰", "5886437972647088483"),
    ("pack_020", "📰", "5893236738372932548"),
    ("pack_021", "📰", "5893057118545646106"),
    ("pack_022", "📝", "5920046907782074235"),
    ("pack_023", "📝", "5922693616953725714"),
    ("pack_024", "🗂", "5766994197705921104"),
    ("pack_025", "↔️", "5893316448670978477"),
    ("pack_026", "↔️", "5895534923833413814"),
    ("pack_027", "⬅️", "5960671702059848143"),
    ("pack_028", "🔁", "6030657343744644592"),
    ("pack_029", "⏫", "5938437708635443119"),
    ("pack_030", "✏️", "6039779802741739617"),
    ("pack_031", "✏", "6039614175917903752"),
    ("pack_032", "❌", "6030757850274336631"),
    ("pack_033", "⌨", "6039404727542747508"),
    ("pack_034", "📎", "6039451237743595514"),
    ("pack_035", "🔧", "5962952497197748583"),
    ("pack_036", "🔨", "5940433880585605708"),
    ("pack_037", "🚪", "6035130900075777681"),
    ("pack_038", "🔎", "6032850693348399258"),
    ("pack_039", "🏷", "5888620056551625531"),
    ("pack_040", "🏷", "5886285355279193209"),
    ("pack_041", "🏷", "5884050696679986441"),
    ("pack_042", "🏷", "5884090188904272855"),
    ("pack_043", "🏷", "5886473311637999700"),
    ("pack_044", "🏷", "5888880224195581055"),
    ("pack_045", "🏷", "5884491244360438851"),
    ("pack_046", "↩️", "5895507195524550741"),
    ("pack_047", "⬅️", "6039539366177541657"),
    ("pack_048", "➡️", "5895383238473421210"),
    ("pack_049", "➡️", "6037622221625626773"),
    ("pack_050", "📺", "6039391078136681499"),
    ("pack_051", "🔗", "6028171274939797252"),
    ("pack_052", "🔗", "6030864215139422409"),
    ("pack_053", "🔗", "5776078972659962594"),
    ("pack_054", "🔗", "5778455936410588193"),
    ("pack_055", "🔗", "5895364284782743985"),
    ("pack_056", "🔗", "5769289093221454192"),
    ("pack_057", "🔗", "5766902139376898645"),
    ("pack_058", "➕", "6032924188828767321"),
    ("pack_059", "ℹ", "6028435952299413210"),
    ("pack_060", "❓", "6030848053177486888"),
    ("pack_061", "❗️", "6030563507299160824"),
    ("pack_062", "▶️", "5773626993010546707"),
    ("pack_063", "▶️", "5850346984501680054"),
    ("pack_064", "❌", "5774077015388852135"),
    ("pack_065", "✅", "5774022692642492953"),
    ("pack_066", "⬆️", "6028205772117118673"),
    ("pack_067", "🔓", "6037496202990194718"),
    ("pack_068", "🔒", "6037249452824072506"),
    ("pack_069", "🖼", "6030466823290360017"),
    ("pack_070", "🖼", "6035128606563241721"),
    ("pack_071", "🤖", "6030400221232501136"),
    ("pack_072", "⭐️", "6030425896546996257"),
    ("pack_073", "⭐️", "6030680867280522811"),
    ("pack_074", "📁", "6037475557082403885"),
    ("pack_075", "📁", "5904219717073114606"),
    ("pack_076", "📄", "6034969813032374911"),
    ("pack_077", "🗑", "6039522349517115015"),
    ("pack_078", "🎶", "6037364759811068375"),
    ("pack_079", "🎶", "6037460610596212193"),
    ("pack_080", "👁", "6037397706505195857"),
    ("pack_081", "👁", "6037243349675544634"),
    ("pack_082", "👁", "5884097155341226387"),
    ("pack_083", "👁", "5935757052042285202"),
    ("pack_084", "⬇️", "6037157012242960559"),
    ("pack_085", "⬇️", "5884218166044791498"),
    ("pack_086", "⬇️", "6039802767931871481"),
    ("pack_087", "☁", "6028115612163641653"),
    ("pack_088", "⬇️", "6032745346390560408"),
    ("pack_089", "⬆️", "5963103826075456248"),
    ("pack_090", "⬇️", "5963087934696459905"),
    ("pack_091", "⬆️", "6039391666547201160"),
    ("pack_092", "📤", "6039573425268201570"),
    ("pack_093", "⬆️", "5776288820467077551"),
    ("pack_094", "🛡", "6030537007350944596"),
    ("pack_095", "🛡", "6032636795387121097"),
    ("pack_096", "🛡", "6030445631921721471"),
    ("pack_097", "📂", "6039630677182254664"),
    ("pack_098", "📂", "6039800856671424701"),
    ("pack_099", "📂", "6037373985400819577"),
    ("pack_100", "📂", "6039348811363520645"),
    ("pack_101", "📥", "6041730074376410123"),
    ("pack_102", "📥", "6039400853482246862"),
    ("pack_103", "📥", "6039420807900303010"),
    ("pack_104", "📤", "6043874504302661409"),
    ("pack_105", "📢", "6021418126061605425"),
    ("pack_106", "📢", "6021681257232994766"),
    ("pack_107", "📢", "6039381989985882045"),
    ("pack_108", "📣", "6039450962865688331"),
    ("pack_109", "📣", "6039422865189638057"),
    ("pack_110", "🔊", "6039454987250044861"),
    ("pack_111", "🔇", "6039505337151655702"),
    ("pack_112", "🔈", "6039853100653612987"),
    ("pack_113", "🔔", "6039486778597970865"),
    ("pack_114", "🔕", "6039569594157371705"),
    ("pack_115", "🔔", "6039636621416993073"),
    ("pack_116", "🔔", "6039677157318332604"),
    ("pack_117", "📷", "6030506650522096180"),
    ("pack_118", "📷", "6030506384234123289"),
    ("pack_119", "📷", "5944753741512052670"),
    ("pack_120", "📷", "5944969250086063864"),
    ("pack_121", "📷", "6048390817033228573"),
    ("pack_122", "📷", "6048808236314791508"),
    ("pack_123", "📷", "6048896442058150625"),
    ("pack_124", "📷", "5778549588172477436"),
    ("pack_125", "🎞", "5944777041709633960"),
    ("pack_126", "🎞", "5945123916153360580"),
    ("pack_127", "❤️", "6037533152593842454"),
    ("pack_128", "❤️", "6035127296598217030"),
    ("pack_129", "📖", "6037286673010660132"),
    ("pack_130", "📖", "6039584437564347225"),
    ("pack_131", "⬅️", "6039519841256214245"),
    ("pack_132", "📷", "5767117162619605573"),
    ("pack_133", "🖼", "5904340783611254165"),
    ("pack_134", "5️⃣", "6035231690073314447"),
    ("pack_135", "2️⃣", "6034847960515219908"),
    ("pack_136", "⏰", "5850317551090800862"),
    ("pack_137", "🕓", "5775896410780079073"),
    ("pack_138", "👣", "5843679481566335204"),
    ("pack_139", "✅️", "5843596438373667352"),
    ("pack_140", "⏲️", "6037268453759389862"),
    ("pack_141", "⏰️", "5983150113483134607"),
    ("pack_142", "⏲️", "5769230088960741619"),
    ("pack_143", "🥇", "6037428784888549034"),
    ("pack_144", "1️⃣", "5940515192906456099"),
    ("pack_145", "2️⃣", "5940420518942347102"),
    ("pack_146", "🎥", "5886579539064132088"),
    ("pack_147", "🎥", "5884252508603289902"),
    ("pack_148", "🎥", "5884351885556585857"),
    ("pack_149", "🎤", "6030722571412967168"),
    ("pack_150", "🎙", "6030467682283818947"),
    ("pack_151", "🎤", "5933678317935791830"),
    ("pack_152", "📞", "6039605143601680423"),
    ("pack_153", "📞", "6037418554276452311"),
    ("pack_154", "☎️", "6039398100408209720"),
    ("pack_155", "🤖", "5983580310292402968"),
    ("pack_156", "💬", "6030784887093464891"),
    ("pack_157", "💬", "6030622631818956594"),
    ("pack_158", "💬", "6030833407339008632"),
    ("pack_159", "💬", "6030329749409108167"),
    ("pack_160", "💬", "6030512294109122096"),
    ("pack_161", "💬", "6030776052345737530"),
    ("pack_162", "💬", "6034831751308644168"),
    ("pack_163", "💡", "5891120964468480450"),
    ("pack_164", "💬", "6037421444789440735"),
    ("pack_165", "💭", "5904248647972820334"),
    ("pack_166", "💬", "6037254263187443802"),
    ("pack_167", "💬", "6035305550625902723"),
    ("pack_168", "💬", "6021618194228187816"),
    ("pack_169", "💤", "5983401171501454028"),
    ("pack_170", "💬", "5778208881301787450"),
    ("pack_171", "💬", "5767244474040192942"),
    ("pack_172", "💬", "5767388406984216738"),
    ("pack_173", "📥", "5776182936638329359"),
    ("pack_174", "💬", "5843918217323484232"),
    ("pack_175", "💬", "5936035091045159318"),
    ("pack_176", "🔒", "5776227595708273495"),
    ("pack_177", "🔒", "5778570255555105942"),
    ("pack_178", "🔞", "5922610170034132416"),
    ("pack_179", "🔞", "5920137394153067262"),
    ("pack_180", "📍", "6030399199030284183"),
    ("pack_181", "📍", "6030418144131026917"),
    ("pack_182", "👤", "6032994772321309200"),
    ("pack_183", "👤", "6032693626394382504"),
    ("pack_184", "👤", "5767278056389480519"),
    ("pack_185", "👤", "5766915217552315762"),
    ("pack_186", "👤", "6035084557378654059"),
    ("pack_187", "👤", "5893192487324880883"),
    ("pack_188", "👤", "6035191085452497972"),
    ("pack_189", "➕", "6033108709213736873"),
    ("pack_190", "👤", "5891207662678317861"),
    ("pack_191", "🗣", "6032653721853234759"),
    ("pack_192", "👥", "6032609071373226027"),
    ("pack_193", "👥", "5879905000972358125"),
    ("pack_194", "👥", "6033125983572201397"),
    ("pack_195", "👥", "6032594876506312598"),
    ("pack_196", "👤", "6041685260687642937"),
    ("pack_197", "👤", "6032608126480421344"),
    ("pack_198", "🎯", "6032949275732742941"),
    ("pack_199", "👥", "5938196735200333756"),
    ("pack_200", "👤", "5778145208411624388"),
    ("pack_201", "👤", "5904630315946611415"),
    ("pack_202", "🎁", "6032644646587338669"),
    ("pack_203", "🎁", "6037175527846975726"),
    ("pack_204", "⭐️", "6034923938486684992"),
    ("pack_205", "🎭", "6032625495328165724"),
    ("pack_206", "🎭", "6032914237389541410"),
    ("pack_207", "🎭", "6032882536235932111"),
    ("pack_208", "🙂", "5774034804450267485"),
    ("pack_209", "🙁", "5778197572652897847"),
    ("pack_210", "🙂", "6028315147754278596"),
    ("pack_211", "🙂", "6039496266180726678"),
    ("pack_212", "🙂", "6039587087559168309"),
    ("pack_213", "😀", "6043996047582170909"),
    ("pack_214", "😀", "5935824500208702046"),
    ("pack_215", "😝", "6043847274210005137"),
    ("pack_216", "🤔", "6043960760130868895"),
    ("pack_217", "😐", "6041748912102968702"),
    ("pack_218", "😨", "6043973168291384891"),
    ("pack_219", "☹️", "6042029429301973188"),
    ("pack_220", "😡", "6044118213631938928"),
    ("pack_221", "🏷", "6039565797406282001"),
    ("pack_222", "🐻", "6044004057696177711"),
    ("pack_223", "🎉", "6041731551845159060"),
    ("pack_224", "👋", "6041921818896372382"),
    ("pack_225", "👍", "6041720006973067267"),
    ("pack_226", "👎", "6041716699848249286"),
    ("pack_227", "👆", "5886676966102274844"),
    ("pack_228", "👆", "5886583490434044162"),
    ("pack_229", "👆", "5886593849895163292"),
    ("pack_230", "👆", "5884106131822875141"),
    ("pack_231", "✋️", "5891184096192763888"),
    ("pack_232", "🚫", "5938215362473496448"),
    ("pack_233", "👏", "5994417835630137549"),
    ("pack_234", "👋", "5985478698722136468"),
    ("pack_235", "🍔", "6041874690220233085"),
    ("pack_236", "🎂", "5922305158636639117"),
    ("pack_237", "🎂", "5922681543800655962"),
    ("pack_238", "🛁", "6041963669057703997"),
    ("pack_239", "🏳️", "6041923781696426657"),
    ("pack_240", "🪧", "6042098561095570207"),
    ("pack_241", "⛱️", "6041933986538721961"),
    ("pack_242", "📍", "6042011682497106307"),
    ("pack_243", "📍", "5983099415689171511"),
    ("pack_244", "🗺", "5904650558127478452"),
    ("pack_245", "🏠", "6042137469204303531"),
    ("pack_246", "🏡", "5938537205847822613"),
    ("pack_247", "💼", "5938492039971737551"),
    ("pack_248", "🎓", "5938195768832692153"),
    ("pack_249", "👓", "5882223295469722324"),
    ("pack_250", "🎶", "5938473438468378529"),
    ("pack_251", "🔫", "5767356727305441799"),
    ("pack_252", "🎞", "5937999673510858217"),
    ("pack_253", "🎮", "5938413566624272793"),
    ("pack_254", "⚽️", "6042069608721027027"),
    ("pack_255", "⚪️", "5884332803016891855"),
    ("pack_256", "⬜️", "5884089033558070257"),
    ("pack_257", "⚪️", "5884094183223857554"),
    ("pack_258", "❤️", "5938368005611195877"),
    ("pack_259", "☁️", "5884330496619450755"),
    ("pack_260", "⭐️", "5886685105065300941"),
    ("pack_261", "🚫", "5938071395169734715"),
    ("pack_262", "📸", "5881806211195605908"),
    ("pack_263", "📸", "5879995903955179148"),
    ("pack_264", "⚡", "5884428842780594914"),
    ("pack_265", "⚡️", "5920515922505765329"),
    ("pack_266", "⚡️", "5922272602784534896"),
    ("pack_267", "🔄", "5769248574499983619"),
    ("pack_268", "#️⃣", "5924498929147189381"),
    ("pack_269", "#️⃣", "5891104106721844396"),
    ("pack_270", "#️⃣", "5850693253355017860"),
    ("pack_271", "💡", "5767288287001580715"),
    ("pack_272", "☀️", "5938525265838739643"),
    ("pack_273", "🌝", "5938342819922973434"),
    ("pack_274", "✅", "6041919344995209164"),
    ("pack_275", "📺", "6044356915029348425"),
    ("pack_276", "🪟", "6035353688619356485"),
    ("pack_277", "🪟", "6033070647213560346"),
    ("pack_278", "🪟", "6034834092065821141"),
    ("pack_279", "📌", "6043896193887506430"),
    ("pack_280", "📌", "6043903233338904177"),
    ("pack_281", "📌", "6041777576714702813"),
    ("pack_282", "⭕️", "5776428312414917091"),
    ("pack_283", "✨", "5778226250149532337"),
    ("pack_284", "✨", "5778647930038653243"),
    ("pack_285", "📎", "5776138384942567185"),
    ("pack_286", "📦", "5778672437122045013"),
    ("pack_287", "📦", "5884479287171485878"),
    ("pack_288", "🖼", "5775903948447682435"),
    ("pack_289", "☀️", "5769527287812723055"),
    ("pack_290", "🌙", "5769143090103193926"),
    ("pack_291", "♾", "6048407885233263063"),
    ("pack_292", "🎙", "6044117517847236354"),
    ("pack_293", "🛜", "6048723247501938454"),
    ("pack_294", "👤", "5884366771913233289"),
    ("pack_295", "✈️", "5927118708873892465"),
    ("pack_296", "🔞", "6050842281286570825"),
    ("pack_297", "📄", "6050643982646513651"),
    ("pack_298", "🔣", "5764638872000533034"),
    ("pack_299", "📷", "5766975922620076409"),
    ("pack_300", "💊", "6050677620830376838"),
    ("pack_301", "✏️", "5771847914477326786"),
    ("pack_302", "💧", "6050632433479455053"),
    ("pack_303", "💧", "6050944866580435869"),
    ("pack_304", "🖍", "5771798621137670637"),
    ("pack_305", "🖌", "6050679691004612757"),
    ("pack_306", "🖌", "6050847684355428245"),
    ("pack_307", "🖌", "5811925731785052842"),
    ("pack_308", "🧽", "5811966564039135541"),
    ("pack_309", "🫥", "5812150667812280629"),
    ("pack_310", "🪄", "6021792097454002931"),
    ("pack_311", "👩‍🎨", "5769635757211784031"),
    ("pack_312", "🖌", "6050877727651664314"),
    ("pack_313", "🔃", "5767310088255576068"),
    ("pack_314", "↩️", "5778432163766604235"),
    ("pack_315", "🔡", "5771851822897566479"),
    ("pack_316", "🔡", "5767262289564536912"),
    ("pack_317", "🅰", "6030710030108463274"),
    ("pack_318", "🅰", "6030437157951246585"),
    ("pack_319", "🅰", "6030764546128352351"),
    ("pack_320", "🅰", "6030474781864759622"),
    ("pack_321", "🗣", "5769500641835618919"),
    ("pack_322", "🗣", "5766919430915232878"),
    ("pack_323", "✂️", "5771880672192893347"),
    ("pack_324", "📝", "5778299625370817409"),
    ("pack_325", "↔️", "5778479949572738874"),
    ("pack_326", "↔️", "5778593237925105705"),
    ("pack_327", "🖼", "5771603848665765302"),
    ("pack_328", "🖼", "5890840627658102916"),
    ("pack_329", "🖼", "5776253421346625666"),
    ("pack_330", "➕", "5882207227997066107"),
    ("pack_331", "📈", "5938539885907415367"),
    ("pack_332", "📈", "5935913431801532272"),
    ("pack_333", "📊", "5936143551854285132"),
    ("pack_334", "🌐", "5776233299424843260"),
    ("pack_335", "🏧", "5879814368572478751"),
    ("pack_336", "💰", "5778421276024509124"),
    ("pack_337", "🪙", "5778613750688911681"),
    ("pack_338", "💎", "5776023601941582822"),
    ("pack_339", "💎", "6037083366438737901"),
    ("pack_340", "💎", "5891105528356018797"),
    ("pack_341", "💎", "6039859895291877126"),
    ("pack_342", "🔨", "6039729023343400390"),
    ("pack_343", "🪙", "5904462880941545555"),
    ("pack_344", "🪙", "5890848474563352982"),
    ("pack_345", "🤑", "5902206159095339799"),
    ("pack_346", "🏪", "5920332557466997677"),
    ("pack_347", "🏷", "5890974664997477030"),
    ("pack_348", "🏷", "5890883384057533697"),
    ("pack_349", "🏷", "5890727932011223292"),
    ("pack_350", "✅", "5938252440926163756"),
    ("pack_351", "🅰", "5769403725898584391"),
    ("pack_352", "📟", "5776118099812028333"),
    ("pack_353", "🌀", "6050588788021793070"),
    ("pack_354", "🔳", "5771652845652677093"),
    ("pack_355", "📅", "5890937706803894250"),
    ("pack_356", "📅", "5891100675042974129"),
    ("pack_357", "⌛️", "5891211339170326418"),
    ("pack_358", "💎", "5769406891289481208"),
    ("pack_359", "👛", "5769126056262898415"),
    ("pack_360", "💰", "5904359114531675993"),
    ("pack_361", "⏲", "6030537810509828330"),
    ("pack_362", "🕶", "5962882510705660145"),
    ("pack_363", "🧠", "5864019342873598613"),
    ("pack_364", "🧭", "6030687898141987254"),
    ("pack_365", "✅", "6030839471832829491"),
    ("pack_366", "🆕", "5895669571058142797"),
    ("pack_367", "🪐", "5891156376473836675"),
    ("pack_368", "✨", "5890925363067886150"),
    ("pack_369", "1⃣", "5794164805065514131"),
    ("pack_370", "2⃣", "5794085322400733645"),
    ("pack_371", "3⃣", "5794280000383358988"),
    ("pack_372", "4⃣", "5794241397217304511"),
    ("pack_373", "5⃣", "5793985348446984682"),
    ("pack_374", "6⃣", "5794324702402976226"),
    ("pack_375", "7⃣", "5793942849745591465"),
    ("pack_376", "8⃣", "5793926687783655907"),
    ("pack_377", "9⃣", "5793979472931723221"),
    ("pack_378", "1⃣", "5794375786743995258"),
    ("pack_379", "2⃣", "5793900634512039101"),
    ("pack_380", "3⃣", "5793981487271386646"),
    ("pack_381", "4⃣", "5794377032284510899"),
    ("pack_382", "5⃣", "5794421072879164075"),
    ("pack_383", "6⃣", "5794125282776456691"),
    ("pack_384", "7⃣", "5793921538117868592"),
    ("pack_385", "8⃣", "5794030230855227946"),
    ("pack_386", "9⃣", "5793886611443817052"),
    ("pack_387", "👑", "5805553606635559688"),
    ("pack_388", "🌟", "5805331990618053402"),
    ("pack_389", "📁", "5805550320985578625"),
    ("pack_390", "📁", "5805648413743651862"),
    ("pack_391", "📁", "5805506958995758422"),
    ("pack_392", "📁", "5805382340519664323"),
    ("pack_393", "🎁", "5805298713211447980"),
    ("pack_394", "🧩", "5837069325034331827"),
    ("pack_395", "🔨", "5836997023554870252"),
    ("pack_396", "🔨", "5836866396419530588"),
    ("pack_397", "💎", "5836907383292436018"),
    ("pack_398", "✨", "5940660740758184142"),
]

# Быстрый доступ (fallback, emoji_id) по ключу пака — используется, чтобы
# назначать эмодзи пака на кнопку/текст сразу из каталога, не заставляя
# админа вручную присылать сам эмодзи-символ.
EMOJI_PACK_MAP: dict[str, tuple[str, str]] = {key: (fallback, emoji_id) for key, fallback, emoji_id in EMOJI_PACK}

# Кнопки, доступные для настройки цвета (Bot API 9.4) и иконки-эмодзи.
# Текст кнопок — без юникод-эмодзи; премиум-иконка ставится отдельно через
# icon_custom_emoji_id (раздел «Премиум-эмодзи» в админке).
STYLE_KEYS: list[tuple[str, str]] = [
    ("connect", "Подключить"),
    ("copy", "Скопировать"),
    ("toggle_on", "Включить"),
    ("toggle_off", "Выключить"),
    ("pay_stars", "Оплата/продление Stars"),
    ("pay_crypto", "Оплата/продление крипто"),
    ("trial", "Пробный период"),
    ("settings", "Настройки"),
    ("back", "Назад"),
    ("home", "Главная"),
    ("instruction", "Инструкция"),
    ("support", "Поддержка"),
    ("support_link", "Ссылка на поддержку"),
    ("oferta", "Оферта"),
    ("admin_price", "Админ: Цена"),
    ("admin_trial", "Админ: Пробный"),
    ("admin_grant", "Админ: Выдать"),
    ("admin_revoke", "Админ: Отобрать"),
    ("admin_broadcast", "Админ: Рассылка"),
    ("admin_photo", "Админ: Фото"),
    ("admin_emoji", "Админ: Эмодзи"),
    ("admin_style", "Админ: Цвет"),
    ("admin_texts", "Админ: Тексты"),
    ("admin_oferta", "Админ: Оферта"),
    ("admin_stats", "Админ: Статистика"),
    ("mode_time", "Режим: Время"),
    ("mode_seconds", "Режим: Секунды"),
    ("mode_date", "Режим: Дата"),
    ("mode_countdown", "Режим: Отсчёт"),
    ("ref_info", "Пригласить друга"),
    ("channel_join", "Подписаться на канал"),
    ("channel_check", "Я подписался"),
    ("admin_channel", "Админ: Канал"),
    ("admin_users", "Админ: Пользователи"),
    ("mute", "Кнопка/статус Мут"),
    ("unmute", "Кнопка Размутить"),
    ("xo_start", "Крестики-нолики: старт"),
    ("xo_cell", "Крестики-нолики: клетка"),
    ("xo_x", "Крестики-нолики: X"),
    ("xo_o", "Крестики-нолики: O"),
    ("xo_new", "Крестики-нолики: новая игра"),
]

# Дефолтные цвета кнопок (Bot API 9.4: primary / success / danger), которые
# применяются один раз при первой инициализации схемы БД, чтобы кнопки не
# были серыми "из коробки". Подобраны по смыслу действия:
#   success — подтверждающие/позитивные действия (включить, оплатить, выдать)
#   danger  — деструктивные/предупреждающие (выключить, отобрать, назад)
#   primary — навигационные/нейтральные действия
DEFAULT_BUTTON_STYLES: dict[str, str] = {
    "connect": "primary",
    "copy": "primary",
    "toggle_on": "success",
    "toggle_off": "danger",
    "pay_stars": "success",
    "pay_crypto": "success",
    "trial": "success",
    "settings": "primary",
    "back": "danger",
    "home": "primary",
    "instruction": "primary",
    "support": "primary",
    "support_link": "primary",
    "oferta": "primary",
    "admin_price": "primary",
    "admin_trial": "primary",
    "admin_grant": "success",
    "admin_revoke": "danger",
    "admin_broadcast": "primary",
    "admin_photo": "primary",
    "admin_emoji": "primary",
    "admin_style": "primary",
    "admin_texts": "primary",
    "admin_oferta": "primary",
    "admin_stats": "primary",
    "mode_time": "primary",
    "mode_seconds": "primary",
    "mode_date": "primary",
    "mode_countdown": "primary",
    "ref_info": "primary",
    "channel_join": "primary",
    "channel_check": "success",
    "admin_channel": "primary",
    "admin_users": "primary",
    "mute": "danger",
    "unmute": "success",
    "xo_start": "primary",
    "xo_cell": "primary",
    "xo_x": "danger",
    "xo_o": "primary",
    "xo_new": "success",
}

# Ключи премиум-эмодзи, которые можно вставлять в ЛЮБОЙ текст бота через
# плейсхолдер {emoji:key}, не только в приветствие. Список используется для
# отображения в админ-разделе «Премиум-эмодзи», сама подстановка работает
# для произвольных ключей — этот список лишь то, что предлагается в UI.
TEXT_EMOJI_KEYS: list[tuple[str, str]] = [
    ("welcome_check", "Приветствие: галочка"),
    ("opt_time", "Приветствие: время"),
    ("opt_date", "Приветствие: дата"),
    ("opt_countdown", "Приветствие: отсчёт"),
    ("star", "Приветствие: звезда"),
    ("toggle_on_status", "Статус: включено"),
    ("toggle_off_status", "Статус: выключено"),
    ("payment_success", "Оплата: успех"),
    ("connected_status", "Подключение: успех"),
]


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_id: int
    db_path: str = "timenick.db"
    timezone_offset_hours: int = 3
    trial_days: int = 1
    default_price_stars: int = 15
    default_price_crypto_usdt: float = 0.5
    cryptobot_token: str = ""
    cryptobot_testnet: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        bot_token = os.getenv("BOT_TOKEN")
        if not bot_token:
            raise RuntimeError("BOT_TOKEN is not set in environment (.env)")

        owner_id_raw = os.getenv("OWNER_ID")
        if not owner_id_raw:
            raise RuntimeError("OWNER_ID is not set in environment (.env)")

        return cls(
            bot_token=bot_token,
            owner_id=int(owner_id_raw),
            db_path=os.getenv("DB_PATH", cls.db_path),
            timezone_offset_hours=int(
                os.getenv("TIMEZONE_OFFSET_HOURS", cls.timezone_offset_hours)
            ),
            trial_days=int(os.getenv("TRIAL_DAYS", cls.trial_days)),
            default_price_stars=int(
                os.getenv("SUB_PRICE_STARS", cls.default_price_stars)
            ),
            default_price_crypto_usdt=float(
                os.getenv("SUB_PRICE_USDT", cls.default_price_crypto_usdt)
            ),
            cryptobot_token=os.getenv("CRYPTOBOT_API_TOKEN", ""),
            cryptobot_testnet=os.getenv("CRYPTOBOT_TESTNET", "false").lower() == "true",
        )


# ======================================================================
# DATABASE
# ======================================================================


class Database:
    def __init__(self, path: str) -> None:
        self._path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self, owner_id: int, trial_days: int, default_price: int, default_price_usdt: float) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT NOT NULL DEFAULT '',
                    username TEXT,
                    business_connection_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    mode TEXT NOT NULL DEFAULT 'time',
                    seconds_enabled INTEGER NOT NULL DEFAULT 0,
                    countdown_target TEXT,
                    countdown_label TEXT,
                    date_format TEXT NOT NULL DEFAULT '%d.%m',
                    trial_used INTEGER NOT NULL DEFAULT 0,
                    sub_until INTEGER,
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS emoji (
                    key TEXT PRIMARY KEY,
                    emoji_id TEXT NOT NULL,
                    fallback TEXT NOT NULL DEFAULT '⭐'
                )
                """
            )
            # Миграция users для БД, созданных до реферальной системы /
            # бесплатной подписки за био / обязательной подписки на канал.
            user_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            user_migrations = {
                "referred_by": "INTEGER",
                "ref_credited": "INTEGER NOT NULL DEFAULT 0",
                "bio_sub_active": "INTEGER NOT NULL DEFAULT 0",
                "channel_checked": "INTEGER NOT NULL DEFAULT 0",
                "start_count": "INTEGER NOT NULL DEFAULT 0",
                "last_start_at": "INTEGER",
            }
            for col, coltype in user_migrations.items():
                if col not in user_cols:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {col} {coltype}")

            # Миграция для БД, созданных до появления колонки fallback.
            existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(emoji)").fetchall()}
            if "fallback" not in existing_cols:
                conn.execute("ALTER TABLE emoji ADD COLUMN fallback TEXT NOT NULL DEFAULT '⭐'")
                # У существующих строк колонка только что появилась с дефолтом
                # '⭐' — для известных ключей это может не совпадать с реальным
                # носителем их emoji_id (например opt_time зарегистрирован на
                # "1️⃣", а не "⭐"). Донасыщаем правильными значениями, чтобы
                # старые записи сразу стали рабочими и не ловили ENTITY_TEXT_INVALID.
                for key, (_id, fallback) in DEFAULT_EMOJI.items():
                    conn.execute(
                        "UPDATE emoji SET fallback = ? WHERE key = ? AND fallback = '⭐'",
                        (fallback, key),
                    )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    method TEXT NOT NULL DEFAULT 'stars',
                    amount TEXT NOT NULL,
                    charge_id TEXT,
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS crypto_invoices (
                    invoice_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS button_styles (
                    key TEXT PRIMARY KEY,
                    style TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS muted_chats (
                    owner_user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    muted_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    PRIMARY KEY (owner_user_id, chat_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS xo_games (
                    owner_user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    board TEXT NOT NULL,
                    turn TEXT NOT NULL DEFAULT 'X',
                    status TEXT NOT NULL DEFAULT 'active',
                    updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    PRIMARY KEY (owner_user_id, chat_id)
                )
                """
            )

            defaults = {
                "price_stars": str(default_price),
                "price_usdt": str(default_price_usdt),
                "trial_days": str(trial_days),
                "broadcast_photo_id": "",
                "oferta_url": "",
                "required_channel": "@sia_channel_1",
            }
            for k, v in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
                )

            for key, (emoji_id, fallback) in DEFAULT_EMOJI.items():
                conn.execute(
                    "INSERT OR IGNORE INTO emoji (key, emoji_id, fallback) VALUES (?, ?, ?)",
                    (key, emoji_id, fallback),
                )

            # Пак готовых премиум-эмодзи (398 шт.) — засеивается один раз,
            # дальше все ключи pack_* сразу доступны как {emoji:pack_xxx}.
            for key, fallback, emoji_id in EMOJI_PACK:
                conn.execute(
                    "INSERT OR IGNORE INTO emoji (key, emoji_id, fallback) VALUES (?, ?, ?)",
                    (key, emoji_id, fallback),
                )

            # Дефолтные цвета кнопок — только если ещё не заданы (INSERT OR
            # IGNORE), чтобы не затирать выбор владельца при повторном запуске.
            for key, style in DEFAULT_BUTTON_STYLES.items():
                conn.execute(
                    "INSERT OR IGNORE INTO button_styles (key, style) VALUES (?, ?)",
                    (key, style),
                )

    # -- users --------------------------------------------------------
    def upsert_user(self, user_id: int, first_name: str, username: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, first_name, username) VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name = excluded.first_name,
                    username = excluded.username
                """,
                (user_id, first_name, username),
            )

    def set_connection(self, user_id: int, connection_id: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET business_connection_id = ? WHERE user_id = ?",
                (connection_id, user_id),
            )

    def set_enabled(self, user_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET enabled = ? WHERE user_id = ?",
                (int(enabled), user_id),
            )

    def set_mode(self, user_id: int, mode: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET mode = ? WHERE user_id = ?", (mode, user_id))

    def set_countdown(self, user_id: int, target_iso: str, label: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET countdown_target = ?, countdown_label = ? WHERE user_id = ?",
                (target_iso, label, user_id),
            )

    def get_user(self, user_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()

    def get_user_by_connection(self, business_connection_id: str) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM users WHERE business_connection_id = ?", (business_connection_id,)
            ).fetchone()

    def get_enabled_users(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM users
                WHERE enabled = 1 AND business_connection_id IS NOT NULL
                """
            ).fetchall()

    def get_all_users(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users").fetchall()

    def count_users(self) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]

    def count_active_subs(self) -> int:
        now = int(time.time())
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE sub_until IS NOT NULL AND sub_until > ?",
                (now,),
            ).fetchone()["c"]

    def get_users_page(self, offset: int, limit: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()

    def register_start(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE users SET start_count = start_count + 1, last_start_at = strftime('%s','now')
                WHERE user_id = ?
                """,
                (user_id,),
            )

    # -- referral system --------------------------------------------------
    def set_referrer(self, user_id: int, referrer_id: int) -> None:
        """Запоминает, кто пригласил пользователя. Срабатывает только один
        раз (пока referred_by ещё NULL) и не позволяет указать себя же."""
        if referrer_id == user_id:
            return
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET referred_by = ? WHERE user_id = ? AND referred_by IS NULL",
                (referrer_id, user_id),
            )

    def credit_referral_if_needed(self, referred_user_id: int, bonus_days: int) -> Optional[int]:
        """Начисляет бонус рефереру ОДИН раз — строго в момент реального
        подключения Business Connection приглашённым (не за /start).
        Возвращает referrer_id, если бонус был начислен, иначе None."""
        row = self.get_user(referred_user_id)
        if not row or not row["referred_by"] or row["ref_credited"]:
            return None
        referrer_id = row["referred_by"]
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET ref_credited = 1 WHERE user_id = ?", (referred_user_id,)
            )
        self.grant_subscription(referrer_id, bonus_days)
        return referrer_id

    def count_referrals(self, referrer_id: int) -> int:
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE referred_by = ? AND ref_credited = 1",
                (referrer_id,),
            ).fetchone()["c"]

    def count_referrals_total(self) -> int:
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE ref_credited = 1"
            ).fetchone()["c"]

    # -- free "bio" subscription ------------------------------------------
    def set_bio_sub(self, user_id: int, active: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET bio_sub_active = ? WHERE user_id = ?", (int(active), user_id)
            )

    def count_bio_sub_active(self) -> int:
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE bio_sub_active = 1"
            ).fetchone()["c"]

    # -- mandatory channel subscription ------------------------------------
    def set_channel_checked(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET channel_checked = 1 WHERE user_id = ?", (user_id,)
            )

    # -- subscription ---------------------------------------------------
    def is_subscribed(self, user_id: int, owner_id: int) -> bool:
        if user_id == owner_id:
            return True
        row = self.get_user(user_id)
        if not row:
            return False
        if row["sub_until"] is None:
            return False
        return row["sub_until"] > int(time.time())

    def grant_subscription(self, user_id: int, days: int) -> int:
        now = int(time.time())
        row = self.get_user(user_id)
        base = now
        if row and row["sub_until"] and row["sub_until"] > now:
            base = row["sub_until"]
        new_until = base + days * 86400
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, first_name) VALUES (?, '')",
                (user_id,),
            )
            conn.execute(
                "UPDATE users SET sub_until = ? WHERE user_id = ?", (new_until, user_id)
            )
        return new_until

    def revoke_subscription(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET sub_until = NULL WHERE user_id = ?", (user_id,)
            )

    def use_trial(self, user_id: int, days: int) -> int:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET trial_used = 1 WHERE user_id = ?", (user_id,)
            )
        return self.grant_subscription(user_id, days)

    def trial_available(self, user_id: int) -> bool:
        row = self.get_user(user_id)
        return bool(row and not row["trial_used"])

    # -- payments -------------------------------------------------------
    def record_payment(self, user_id: int, method: str, amount: str, charge_id: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO payments (user_id, method, amount, charge_id) VALUES (?, ?, ?, ?)",
                (user_id, method, amount, charge_id),
            )

    def total_stars_earned(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(CAST(amount AS INTEGER)),0) AS s FROM payments WHERE method='stars'"
            ).fetchone()
            return row["s"]

    def total_crypto_payments(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM payments WHERE method='crypto'"
            ).fetchone()
            return row["c"]

    # -- crypto invoices --------------------------------------------------
    def save_crypto_invoice(self, invoice_id: str, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO crypto_invoices (invoice_id, user_id, status) VALUES (?, ?, 'active')",
                (invoice_id, user_id),
            )

    def mark_crypto_invoice(self, invoice_id: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE crypto_invoices SET status = ? WHERE invoice_id = ?",
                (status, invoice_id),
            )

    def get_active_crypto_invoices(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM crypto_invoices WHERE status = 'active'"
            ).fetchall()

    # -- settings ---------------------------------------------------------
    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_price_stars(self) -> int:
        return int(self.get_setting("price_stars", "15"))

    def get_price_usdt(self) -> float:
        return float(self.get_setting("price_usdt", "0.5"))

    def get_trial_days(self) -> int:
        return int(self.get_setting("trial_days", "1"))

    # -- emoji --------------------------------------------------------------
    def get_emoji(self, key: str) -> Optional[str]:
        """Возвращает только emoji_id (для обратной совместимости с местами,
        где нужен просто ID, например icon_custom_emoji_id у кнопок)."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT emoji_id FROM emoji WHERE key = ?", (key,)
            ).fetchone()
            return row["emoji_id"] if row else None

    def get_emoji_full(self, key: str) -> Optional[tuple[str, str]]:
        """Возвращает (emoji_id, fallback) — fallback обязателен для
        корректной вставки <tg-emoji> в текст (см. render_emoji_tags)."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT emoji_id, fallback FROM emoji WHERE key = ?", (key,)
            ).fetchone()
            if not row:
                return None
            return row["emoji_id"], (row["fallback"] or "⭐")

    def set_emoji(self, key: str, emoji_id: str, fallback: str = "⭐") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO emoji (key, emoji_id, fallback) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET emoji_id = excluded.emoji_id, fallback = excluded.fallback
                """,
                (key, emoji_id, fallback),
            )

    def get_all_emoji(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM emoji ORDER BY key").fetchall()

    # -- button styles (Bot API 9.4) -----------------------------------------
    def get_button_style(self, key: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT style FROM button_styles WHERE key = ?", (key,)
            ).fetchone()
            return row["style"] if row else None

    def set_button_style(self, key: str, style: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO button_styles (key, style) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET style = excluded.style
                """,
                (key, style),
            )

    # -- mute (программная имитация мута собеседника в личном бизнес-чате) --
    def is_chat_muted(self, owner_user_id: int, chat_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM muted_chats WHERE owner_user_id = ? AND chat_id = ?",
                (owner_user_id, chat_id),
            ).fetchone()
            return row is not None

    def mute_chat(self, owner_user_id: int, chat_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO muted_chats (owner_user_id, chat_id) VALUES (?, ?)",
                (owner_user_id, chat_id),
            )

    def unmute_chat(self, owner_user_id: int, chat_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM muted_chats WHERE owner_user_id = ? AND chat_id = ?",
                (owner_user_id, chat_id),
            )

    # -- xo (крестики-нолики 6x6 в личном бизнес-чате) -------------------
    def xo_get(self, owner_user_id: int, chat_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM xo_games WHERE owner_user_id = ? AND chat_id = ?",
                (owner_user_id, chat_id),
            ).fetchone()

    def xo_new_game(self, owner_user_id: int, chat_id: int) -> None:
        board = "." * 36
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO xo_games (owner_user_id, chat_id, board, turn, status, updated_at)
                VALUES (?, ?, ?, 'X', 'active', strftime('%s','now'))
                ON CONFLICT(owner_user_id, chat_id) DO UPDATE SET
                    board = excluded.board, turn = 'X', status = 'active',
                    updated_at = strftime('%s','now')
                """,
                (owner_user_id, chat_id, board),
            )

    def xo_save(self, owner_user_id: int, chat_id: int, board: str, turn: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE xo_games SET board = ?, turn = ?, status = ?, updated_at = strftime('%s','now')
                WHERE owner_user_id = ? AND chat_id = ?
                """,
                (board, turn, status, owner_user_id, chat_id),
            )


# ======================================================================
# CRYPTOBOT (Crypto Pay API) CLIENT
# ======================================================================


class CryptoPayError(Exception):
    pass


class CryptoBotClient:
    """Minimal async client for the @CryptoBot Crypto Pay API.
    Docs: https://help.crypt.bot/crypto-pay-api
    """

    def __init__(self, token: str, testnet: bool = False) -> None:
        self._token = token
        self._base_url = (
            "https://testnet-pay.crypt.bot/api" if testnet else "https://pay.crypt.bot/api"
        )

    @property
    def configured(self) -> bool:
        return bool(self._token)

    async def _request(self, method: str, params: dict) -> dict:
        if not self._token:
            raise CryptoPayError("CRYPTOBOT_API_TOKEN не задан в .env")
        headers = {"Crypto-Pay-API-Token": self._token}
        url = f"{self._base_url}/{method}"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
        if not data.get("ok"):
            raise CryptoPayError(str(data.get("error", data)))
        return data["result"]

    async def create_invoice(self, amount: float, description: str, payload: str) -> dict:
        return await self._request(
            "createInvoice",
            {
                "currency_type": "crypto",
                "asset": "USDT",
                "amount": str(amount),
                "description": description,
                "payload": payload,
                "expires_in": 1800,
            },
        )

    async def get_invoice_status(self, invoice_id: str) -> Optional[str]:
        result = await self._request("getInvoices", {"invoice_ids": str(invoice_id)})
        items = result.get("items", [])
        if not items:
            return None
        return items[0].get("status")


# ======================================================================
# NICKNAME CLOCK
# ======================================================================


def format_label(row, tz: timezone) -> str:
    mode = row["mode"] or "time"
    now = datetime.now(tz)

    if mode == "seconds":
        return now.strftime("• [%H:%M:%S]")

    if mode == "date":
        fmt = row["date_format"] or "%d.%m"
        return f"• [{now.strftime(fmt)}]"

    if mode == "countdown":
        target_iso = row["countdown_target"]
        label = row["countdown_label"] or "Отсчёт"
        if not target_iso:
            return now.strftime("• [%H:%M]")
        try:
            target = datetime.fromisoformat(target_iso)
            if target.tzinfo is None:
                target = target.replace(tzinfo=tz)
        except ValueError:
            return now.strftime("• [%H:%M]")

        delta = target - now
        if delta.total_seconds() <= 0:
            return f"• [{label}: сегодня!]"
        days = delta.days
        hours = delta.seconds // 3600
        if days > 0:
            return f"• [{label}: {days}д {hours}ч]"
        minutes = (delta.seconds % 3600) // 60
        return f"• [{label}: {hours}ч {minutes}м]"

    return now.strftime("• [%H:%M]")


class NicknameClock:
    def __init__(self, bot: Bot, db: Database, tz_offset_hours: int, owner_id: int = 0) -> None:
        self._bot = bot
        self._db = db
        self._tz = timezone(timedelta(hours=tz_offset_hours))
        self._last_applied: dict[int, str] = {}
        self._owner_id = owner_id

    async def apply(self, row) -> None:
        connection_id = row["business_connection_id"]
        if not connection_id:
            return

        user_id = row["user_id"]

        if user_id != self._owner_id:
            has_paid = bool(row["sub_until"] and row["sub_until"] > int(time.time()))
            if not has_paid:
                self._db.set_enabled(user_id, False)
                await self.clear(user_id, connection_id, row["first_name"] or "")
                try:
                    await self._bot.send_message(
                        user_id,
                        "⏳ Подписка закончилась. Функция отключена.",
                    )
                except Exception:
                    logger.exception("Failed to notify user_id=%s about subscription expiry", user_id)
                return

        label = format_label(row, self._tz)
        if self._last_applied.get(user_id) == label:
            return

        try:
            await self._bot(
                SetBusinessAccountName(
                    business_connection_id=connection_id,
                    first_name=row["first_name"] or "",
                    last_name=label,
                )
            )
            self._last_applied[user_id] = label
        except TelegramRetryAfter:
            # Flood control — не ошибка прав, просто подождём и попробуем
            # на следующем цикле обновления.
            logger.warning("Flood control while updating nickname for user_id=%s, will retry", user_id)
        except TelegramNetworkError:
            # Временный сетевой сбой/таймаут — не трогаем настройки
            # пользователя, повторим на следующем цикле.
            logger.warning("Network error while updating nickname for user_id=%s, will retry", user_id)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.exception("Failed to update nickname for user_id=%s", user_id)
            if self._is_permission_error(exc):
                await self._handle_permission_loss(user_id)
            # Иначе — прочая ошибка Telegram (например, временная проблема
            # на их стороне), не связанная с правами: не отключаем функцию.
        except Exception:
            # Неизвестная ошибка — логируем, но не считаем автоматически
            # потерей прав, чтобы не отключать функцию у пользователя зря.
            logger.exception("Unexpected error updating nickname for user_id=%s", user_id)

    @staticmethod
    def _is_permission_error(exc: Exception) -> bool:
        """True только если Telegram реально жалуется на нехватку прав
        бизнес-подключения (right/rights/permission в тексте ошибки), а не
        на что-то другое (лимиты, временный сбой и т.п.)."""
        if isinstance(exc, TelegramForbiddenError):
            return True
        text = str(exc).lower()
        return any(hint in text for hint in ("right", "permission", "not enough"))

    async def _handle_permission_loss(self, user_id: int) -> None:
        self._db.set_enabled(user_id, False)
        try:
            await self._bot.send_message(
                user_id,
                "Недостаточно прав для смены фамилии. "
                "Переподключите бота в настройках, разрешив изменение имени.",
            )
        except Exception:
            logger.exception("Failed to notify user_id=%s about permission loss", user_id)

    async def clear(self, user_id: int, connection_id: str, first_name: str) -> None:
        if not connection_id:
            return
        try:
            await self._bot(
                SetBusinessAccountName(
                    business_connection_id=connection_id,
                    first_name=first_name,
                    last_name="",
                )
            )
        except Exception:
            logger.exception("Failed to clear nickname for user_id=%s", user_id)
        finally:
            self._last_applied.pop(user_id, None)


def seconds_until_next_minute(tz: timezone) -> float:
    now = datetime.now(tz)
    next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return (next_minute - now).total_seconds() + 0.05


async def run_update_loop(db: Database, clock: NicknameClock, tz: timezone) -> None:
    """Раз в минуту обновляет ник для всех режимов, КРОМЕ 'seconds' —
    у него свой, более частый цикл (см. run_seconds_loop)."""
    while True:
        try:
            delay = seconds_until_next_minute(tz)
            await asyncio.sleep(delay)
            for row in db.get_enabled_users():
                if (row["mode"] or "time") == "seconds":
                    continue
                try:
                    await clock.apply(row)
                except Exception:
                    logger.exception("apply() failed for user_id=%s", row["user_id"])
        except Exception:
            logger.exception("run_update_loop iteration crashed, continuing")
            await asyncio.sleep(5)


async def run_seconds_loop(db: Database, clock: NicknameClock) -> None:
    """Пользователи в режиме 'Время с секундами' обновляются раз в 12
    секунд (а не каждую секунду — иначе легко словить лимиты Business API)."""
    while True:
        try:
            await asyncio.sleep(12)
            for row in db.get_enabled_users():
                if (row["mode"] or "time") != "seconds":
                    continue
                try:
                    await clock.apply(row)
                except Exception:
                    logger.exception("apply() failed for user_id=%s", row["user_id"])
        except Exception:
            logger.exception("run_seconds_loop iteration crashed, continuing")
            await asyncio.sleep(5)


async def run_crypto_poll_loop(
    db: Database, crypto: CryptoBotClient, bot: Bot, settings: Settings
) -> None:
    """Poll active CryptoBot invoices for payment status (no webhook server
    required — simpler for a single-file deployment)."""
    if not crypto.configured:
        return
    while True:
        try:
            await asyncio.sleep(10)
            for inv in db.get_active_crypto_invoices():
                try:
                    await _poll_one_invoice(db, crypto, bot, settings, inv)
                except Exception:
                    logger.exception("Failed polling invoice_id=%s", inv["invoice_id"])
        except Exception:
            logger.exception("run_crypto_poll_loop iteration crashed, continuing")
            await asyncio.sleep(5)


async def _poll_one_invoice(
    db: Database, crypto: CryptoBotClient, bot: Bot, settings: Settings, inv: sqlite3.Row
) -> None:
    try:
        status = await crypto.get_invoice_status(inv["invoice_id"])
    except CryptoPayError:
        return
    if status == "paid":
        db.mark_crypto_invoice(inv["invoice_id"], "paid")
        user_id = inv["user_id"]
        price = db.get_price_usdt()
        db.record_payment(user_id, "crypto", str(price), inv["invoice_id"])
        new_until = db.grant_subscription(user_id, 30)
        until_str = datetime.fromtimestamp(new_until).strftime("%d.%m.%Y")
        try:
            await bot.send_message(
                user_id,
                f"✅ <b>Оплата криптой прошла успешно!</b>\nПодписка активна до <b>{until_str}</b>.",
                parse_mode="HTML",
            )
            await show_start_screen(bot, db, settings, user_id)
        except Exception:
            logger.exception("Failed to notify user_id=%s about crypto payment", user_id)
    elif status == "expired":
        db.mark_crypto_invoice(inv["invoice_id"], "expired")


# ======================================================================
# TEXTS / EMOJI RENDERING
# ======================================================================


_EMOJI_PLACEHOLDER_RE = re.compile(r"\{emoji:([a-zA-Z0-9_]+)\}")


def _safe_format(template: str, **kwargs) -> str:
    """Как str.format(**kwargs), но:
    1) не падает, если шаблон (введённый админом) содержит неизвестные
       {плейсхолдеры} или одиночные фигурные скобки — они остаются как есть;
    2) не трогает {emoji:key} — они разбираются отдельно в render_emoji_tags,
       после форматирования, чтобы .format() их не считал полем формата."""
    # Временно "прячем" {emoji:...}, чтобы .format() их не пытался разрешить.
    placeholders: list[str] = []

    def _stash(match: "re.Match[str]") -> str:
        placeholders.append(match.group(0))
        return f"\x00{len(placeholders) - 1}\x00"

    stashed = _EMOJI_PLACEHOLDER_RE.sub(_stash, template)

    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    try:
        formatted = stashed.format_map(_SafeDict(**kwargs))
    except (ValueError, IndexError):
        # Одиночные "{" / "}" в тексте, введённом админом — оставляем как есть.
        formatted = stashed

    for i, placeholder in enumerate(placeholders):
        formatted = formatted.replace(f"\x00{i}\x00", placeholder)
    return formatted


def render_emoji_tags(db: Database, text: str) -> str:
    """Заменяет любые плейсхолдеры {emoji:key} в произвольном HTML-тексте
    на <tg-emoji emoji-id="...">, если для ключа задан премиум-эмодзи в БД.
    Если эмодзи для ключа не задано, плейсхолдер просто вырезается (пустая
    строка), чтобы не оставлять "сырой" текст в сообщении пользователю.
    Работает для ЛЮБОГО текста бота, а не только для приветствия — админ
    может вставить {emoji:key} в любой шаблон текста через настройки.

    ВАЖНО: Telegram требует, чтобы содержимым тега <tg-emoji> был ТОТ САМЫЙ
    стандартный юникод-эмодзи, поверх которого зарегистрирован конкретный
    custom_emoji_id (не произвольный символ) — иначе Telegram отклоняет ВСЁ
    сообщение с ошибкой ENTITY_TEXT_INVALID. Поэтому берём fallback именно
    из БД (сохранённый вместе с ID при загрузке премиум-эмодзи), а не
    подставляем одинаковый символ для всех ключей."""

    def _replace(match: "re.Match[str]") -> str:
        key = match.group(1)
        pair = db.get_emoji_full(key)
        if not pair or not pair[0]:
            return ""
        emoji_id, fallback = pair
        return f'<tg-emoji emoji-id="{escape(emoji_id)}">{escape(fallback)}</tg-emoji>'

    return _EMOJI_PLACEHOLDER_RE.sub(_replace, text)


def _days_word(n: int) -> str:
    if n == 1:
        return "день"
    if 2 <= n <= 4:
        return "дня"
    return "дней"


def welcome_text(db: Database) -> str:
    # Дефолтный шаблон приветствия хранится в settings под ключом
    # "welcome_template", чтобы владелец мог полностью переписать текст,
    # вставляя {emoji:key} где угодно (не только в начале, как раньше).
    price_stars = db.get_price_stars()
    price_usdt = db.get_price_usdt()
    trial_days = db.get_trial_days()

    default_template = (
        "{emoji:welcome_check} <b>Здравствуй!</b>\n\n"
        "Это бот <b>Time</b>, который может ставить в ваш никнейм:\n\n"
        "{emoji:opt_time} Время\n"
        "{emoji:opt_date} Дату\n"
        "{emoji:opt_countdown} Обратный отсчёт\n\n"
        "И многое другое!\n\n"
        "{emoji:star} <b>{trial_days} {trial_word} подписки бесплатно</b>, "
        "потом всего <b>{price_stars}⭐️/мес</b> или <b>{price_usdt}$ в крипте</b>."
    )
    template = db.get_setting("welcome_template", "") or default_template
    text = _safe_format(
        template,
        trial_days=trial_days,
        trial_word=_days_word(trial_days),
        price_stars=price_stars,
        price_usdt=price_usdt,
    )
    return render_emoji_tags(db, text)


def instruction_text(db: Database) -> str:
    price_stars = db.get_price_stars()
    price_usdt = db.get_price_usdt()
    trial_days = db.get_trial_days()
    default_template = (
        "<b>Инструкция по боту Time</b>\n\n"
        "<b>1. Подключение</b>\n"
        "Нажмите «Подключить» → «Скопировать» → в Telegram откройте "
        "Настройки → Telegram для бизнеса → Чат-боты → Добавить бота → вставьте скопированный текст → "
        "разрешите пункты «Управлять профилем» и «Удалять все сообщения» "
        "(последнее нужно для команд .mute и .spam — без него бот не сможет "
        "удалять сообщения собеседника).\n\n"
        "<b>2. Режимы отображения в нике</b>\n"
        "Время — часы и минуты (ЧЧ:ММ)\n"
        "Время с секундами\n"
        "Дата\n"
        "Обратный отсчёт — до указанной даты или события\n"
        "Выбираются в разделе «Настройки».\n\n"
        "<b>3. Включение и выключение</b>\n"
        "Кнопка «Включить» / «Выключить» на главном экране запускает и "
        "останавливает обновление ника.\n\n"
        "<b>4. Мут и спам в личных чатах</b>\n"
        "Напишите <b>.mute</b> собеседнику в любом личном чате — команда удалится, "
        "собеседнику придёт «Помолчи», и все его сообщения будут удаляться, пока "
        "не нажмёте «Размутить» (размутить может только владелец аккаунта). "
        "Команда <b>.spam 20 Привет</b> удалит саму команду "
        "и отправит текст «Привет» 20 раз подряд (максимум 100 за раз).\n\n"
        "<b>5. Игра «Крестики-нолики»</b>\n"
        "Напишите <b>.xo</b> собеседнику — начнётся игра на поле 6×6, побеждает тот, "
        "кто первым выстроит 4 своих знака подряд. Владелец аккаунта играет за ❌, "
        "собеседник — за ⭕️, ходите по очереди нажатием на клетки.\n\n"
        "<b>6. Подписка</b>\n"
        "Первые {trial_days} {trial_word} — бесплатно (кнопка «Пробный день»). "
        "Далее — {price_stars}⭐️ или {price_usdt}$ в крипте за 30 дней.\n"
        "Продлить или оплатить можно кнопками на главном экране в любой момент.\n\n"
        "<b>7. Поддержка</b>\n"
        "Если что-то не работает — раздел «Поддержка» на главном экране."
    )
    template = db.get_setting("instruction_template", "") or default_template
    text = _safe_format(
        template,
        trial_days=trial_days,
        trial_word=_days_word(trial_days),
        price_stars=price_stars,
        price_usdt=price_usdt,
    )
    return render_emoji_tags(db, text)


def support_text(db: Database) -> str:
    default_template = (
        "<b>Поддержка</b>\n\n"
        "<b>Частые вопросы:</b>\n\n"
        "• <b>Бот не подключается</b> — проверьте, что при добавлении бота в "
        "Telegram для бизнеса разрешён пункт «Управлять профилем».\n\n"
        "• <b>Ник не обновляется сразу</b> — обновление происходит раз в минуту, "
        "это ограничение Telegram Business API, подождите немного.\n\n"
        "• <b>Не проходит оплата</b> — попробуйте другой способ (Stars или крипта) "
        "либо повторите попытку чуть позже.\n\n"
        "• <b>Закончилась подписка</b> — продлите её кнопкой «Продлить» на главном экране.\n\n"
        "Если вопрос не решён — напишите нам напрямую:"
    )
    template = db.get_setting("support_template", "") or default_template
    return render_emoji_tags(db, template)


def mute_text(db: Database) -> str:
    """Текст, который получает собеседник после команды .mute. Полностью
    настраивается через админку («Тексты бота»), поддерживает {emoji:ключ}."""
    default_template = "Помолчи"
    template = db.get_setting("mute_template", "") or default_template
    return render_emoji_tags(db, template)


def unmute_button_text(db: Database) -> str:
    """Текст кнопки «Размутить». Кнопки не поддерживают HTML/premium-emoji
    entities внутри текста — только plain-текст, поэтому храним отдельно
    от message-шаблонов и не прогоняем через render_emoji_tags."""
    return db.get_setting("unmute_button_text", "") or "Размутить"


def build_not_connected_text(db: Database) -> str:
    default_template = (
        "<b>Бот не подключён.</b>\n\n"
        "Нажмите на кнопку <b>Подключить</b>, затем на кнопку <b>Скопировать</b>, "
        "далее — <b>Автоматизация чатов</b>, вставьте текст, который вы скопировали, "
        "и нажмите <b>Добавить</b>. Дальше разрешите <b>Управлять профилем</b>."
    )
    template = db.get_setting("not_connected_template", "") or default_template
    return render_emoji_tags(db, template)


# ======================================================================
# KEYBOARDS
# ======================================================================


def _btn(db: Database, key: str, text: str, **kwargs) -> InlineKeyboardButton:
    style = db.get_button_style(key)
    icon = db.get_emoji(f"btn_{key}")
    if style:
        kwargs["style"] = style
    if icon:
        kwargs["icon_custom_emoji_id"] = icon
    return InlineKeyboardButton(text=text, **kwargs)


def build_info_row(db: Database) -> list[InlineKeyboardButton]:
    return [
        _btn(db, "instruction", "Инструкция", callback_data="show_instruction"),
        _btn(db, "support", "Поддержка", callback_data="show_support"),
    ]


# ----------------------------------------------------------------------
# Крестики-нолики 6x6 (команда .xo в личном бизнес-чате).
# Поле 6x6 = 36 клеток, board хранится строкой длиной 36 из символов
# '.'/'X'/'O'. Победа — 4 в ряд (по горизонтали/вертикали/диагонали),
# что для поля 6x6 играется интереснее и дольше, чем классические 3.
# ----------------------------------------------------------------------
_XO_SIZE = 6
_XO_WIN_LEN = 4


def _xo_check_winner(board: str) -> Optional[str]:
    def cell(r: int, c: int) -> str:
        return board[r * _XO_SIZE + c]

    directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
    for r in range(_XO_SIZE):
        for c in range(_XO_SIZE):
            sym = cell(r, c)
            if sym == ".":
                continue
            for dr, dc in directions:
                end_r = r + dr * (_XO_WIN_LEN - 1)
                end_c = c + dc * (_XO_WIN_LEN - 1)
                if not (0 <= end_r < _XO_SIZE and 0 <= end_c < _XO_SIZE):
                    continue
                if all(cell(r + dr * i, c + dc * i) == sym for i in range(_XO_WIN_LEN)):
                    return sym
    return None


def _xo_is_full(board: str) -> bool:
    return "." not in board


def _xo_status_text(db: Database, row: sqlite3.Row, owner_name: str, guest_name: str) -> str:
    status = row["status"]
    if status == "won_X":
        return f"{{emoji:xo_x}} Победа: <b>{escape(owner_name)}</b> (X)!"
    if status == "won_O":
        return f"{{emoji:xo_o}} Победа: <b>{escape(guest_name)}</b> (O)!"
    if status == "draw":
        return "🤝 Ничья!"
    turn = row["turn"]
    who = f"<b>{escape(owner_name)}</b> (X)" if turn == "X" else f"<b>{escape(guest_name)}</b> (O)"
    return f"🎮 Крестики-нолики 6×6. Ходит: {who}"


def _xo_board_keyboard(db: Database, chat_id: int, row: sqlite3.Row) -> InlineKeyboardMarkup:
    board = row["board"]
    status = row["status"]
    kb_rows: list[list[InlineKeyboardButton]] = []
    for r in range(_XO_SIZE):
        line: list[InlineKeyboardButton] = []
        for c in range(_XO_SIZE):
            idx = r * _XO_SIZE + c
            sym = board[idx]
            if sym == "X":
                line.append(_btn(db, "xo_x", "❌", callback_data="noop"))
            elif sym == "O":
                line.append(_btn(db, "xo_o", "⭕️", callback_data="noop"))
            elif status == "active":
                line.append(_btn(db, "xo_cell", "·", callback_data=f"xo_mv:{chat_id}:{idx}"))
            else:
                line.append(_btn(db, "xo_cell", "·", callback_data="noop"))
        kb_rows.append(line)
    if status != "active":
        kb_rows.append([_btn(db, "xo_new", "🔄 Новая игра", callback_data=f"xo_new:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)


def build_oferta_row(db: Database) -> list[list[InlineKeyboardButton]]:
    url = db.get_setting("oferta_url", "")
    if not url:
        return []
    return [[_btn(db, "oferta", "Публичная оферта", url=url)]]


def build_instruction_keyboard(db: Database) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(db, "back", "Закрыть", callback_data="close_info")]]
    )


def build_support_keyboard(db: Database) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(db, "support_link", "Написать в поддержку", url="https://t.me/deverskyi")],
            [_btn(db, "back", "Закрыть", callback_data="close_info")],
        ]
    )


def build_toggle_keyboard(db: Database, enabled: bool) -> InlineKeyboardMarkup:
    if enabled:
        btn = _btn(db, "toggle_off", "Выключить", callback_data="toggle_off")
    else:
        btn = _btn(db, "toggle_on", "Включить", callback_data="toggle_on")
    price_stars = db.get_price_stars()
    price_usdt = db.get_price_usdt()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn],
            [_btn(db, "pay_stars", f"Продлить подписку — {price_stars}⭐️", callback_data="pay_stars")],
            [_btn(db, "pay_crypto", f"Оплатить подписку — {price_usdt}$ крипта", callback_data="pay_crypto")],
            [_btn(db, "settings", "Настройки", callback_data="open_settings")],
            [_btn(db, "ref_info", "🔗 Пригласить друга", callback_data="ref_info")],
            build_info_row(db),
            *build_oferta_row(db),
        ]
    )


def build_connect_keyboard(db: Database, bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(db, "connect", "Подключить", url="tg://settings/edit")],
            [_btn(db, "copy", "Скопировать", copy_text=CopyTextButton(text=f"@{bot_username}"))],
        ]
    )


def build_channel_gate_keyboard(db: Database, channel: str) -> InlineKeyboardMarkup:
    handle = channel.lstrip("@")
    link = channel if channel.startswith("http") else f"https://t.me/{handle}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(db, "channel_join", "Подписаться на канал", url=link)],
            [_btn(db, "channel_check", "✅ Я подписался", callback_data="check_channel")],
        ]
    )


def build_welcome_keyboard(db: Database, has_trial: bool, is_subscribed: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    price_stars = db.get_price_stars()
    price_usdt = db.get_price_usdt()
    if not is_subscribed and has_trial:
        rows.append([_btn(db, "trial", "Пробный день бесплатно", callback_data="use_trial")])
    stars_label = f"Продлить подписку — {price_stars}⭐️" if is_subscribed else f"Оплатить подписку — {price_stars}⭐️"
    rows.append([_btn(db, "pay_stars", stars_label, callback_data="pay_stars")])
    rows.append([_btn(db, "pay_crypto", f"Оплатить подписку — {price_usdt}$ крипта", callback_data="pay_crypto")])
    rows.append([_btn(db, "settings", "Настройки", callback_data="open_settings")])
    rows.append([_btn(db, "ref_info", "🔗 Пригласить друга", callback_data="ref_info")])
    rows.append(build_info_row(db))
    rows.extend(build_oferta_row(db))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_main_reply_keyboard(db: Database) -> ReplyKeyboardMarkup:
    style = db.get_button_style("home")
    icon = db.get_emoji("btn_home")
    kwargs = {}
    if style:
        kwargs["style"] = style
    if icon:
        kwargs["icon_custom_emoji_id"] = icon
    button = KeyboardButton(text="Главная", **kwargs)
    return ReplyKeyboardMarkup(keyboard=[[button]], resize_keyboard=True, is_persistent=True)


def build_settings_keyboard(db: Database, user_row) -> InlineKeyboardMarkup:
    # Подсветка выбранного режима теперь делается цветом кнопки (success),
    # а не эмодзи-меткой в тексте.
    mode = user_row["mode"] if user_row else "time"

    def _mode_btn(key: str, mode_value: str, text: str, callback: str) -> InlineKeyboardButton:
        is_selected = mode == mode_value
        style = "success" if is_selected else db.get_button_style(key)
        icon = db.get_emoji(f"btn_{key}")
        kwargs: dict = {"callback_data": callback}
        if style:
            kwargs["style"] = style
        if icon:
            kwargs["icon_custom_emoji_id"] = icon
        return InlineKeyboardButton(text=text, **kwargs)

    rows = [
        [_mode_btn("mode_time", "time", "Время", "mode_time")],
        [_mode_btn("mode_seconds", "seconds", "Время с секундами", "mode_seconds")],
        [_mode_btn("mode_date", "date", "Дата", "mode_date")],
        [_mode_btn("mode_countdown", "countdown", "Обратный отсчёт", "mode_countdown")],
        [_btn(db, "back", "Назад", callback_data="back_to_start")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_menu_keyboard(db: Database) -> InlineKeyboardMarkup:
    rows = [
        [_btn(db, "admin_price", "Цена подписки", callback_data="admin_price")],
        [_btn(db, "admin_trial", "Длительность пробного периода", callback_data="admin_trial")],
        [_btn(db, "admin_grant", "Выдать подписку", callback_data="admin_grant")],
        [_btn(db, "admin_revoke", "Отобрать подписку", callback_data="admin_revoke")],
        [_btn(db, "admin_broadcast", "Рассылка", callback_data="admin_broadcast")],
        [_btn(db, "admin_photo", "Фото приветствия", callback_data="admin_photo")],
        [_btn(db, "admin_oferta", "Ссылка на оферту", callback_data="admin_oferta")],
        [_btn(db, "admin_emoji", "Премиум-эмодзи", callback_data="admin_emoji")],
        [_btn(db, "admin_style", "Цвет кнопок", callback_data="admin_style")],
        [_btn(db, "admin_texts", "Тексты бота", callback_data="admin_texts")],
        [_btn(db, "admin_channel", "Обязательный канал", callback_data="admin_channel")],
        [_btn(db, "admin_users", "Пользователи", callback_data="admin_users:0")],
        [_btn(db, "admin_stats", "Статистика", callback_data="admin_stats")],
        [_btn(db, "back", "Назад", callback_data="back_to_start")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_back_keyboard(db: Database, callback_data: str = "admin_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(db, "back", "Назад", callback_data=callback_data)]])


# ======================================================================
# FSM STATES
# ======================================================================


class AdminStates(StatesGroup):
    waiting_price = State()
    waiting_price_usdt = State()
    waiting_trial_days = State()
    waiting_grant_id = State()
    waiting_grant_days = State()
    waiting_revoke_id = State()
    waiting_broadcast_text = State()
    waiting_broadcast_photo = State()
    waiting_photo = State()
    waiting_emoji_value = State()
    waiting_oferta_url = State()
    waiting_text_template = State()
    waiting_channel = State()


class UserStates(StatesGroup):
    waiting_countdown_target = State()


# ======================================================================
# START SCREEN (shared by /start, "Главная" button, and post-payment)
# ======================================================================


async def passes_channel_gate(bot: Bot, db: Database, user_id: int) -> bool:
    """Обязательная подписка на канал — проверяется только один раз, при
    первом /start (см. channel_checked). Если канал в админке не задан,
    или проверить членство не удалось (например бот не админ канала),
    пользователя не блокируем."""
    channel = db.get_setting("required_channel", "").strip()
    if not channel:
        return True
    row = db.get_user(user_id)
    if row and row["channel_checked"]:
        return True
    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
        status = member.status
    except Exception:
        logger.exception("Failed to check channel membership for user_id=%s", user_id)
        db.set_channel_checked(user_id)
        return True
    if status in ("member", "administrator", "creator"):
        db.set_channel_checked(user_id)
        return True
    return False


async def show_start_screen(bot: Bot, db: Database, settings: Settings, user_id: int) -> None:
    row = db.get_user(user_id)
    is_connected = bool(row and row["business_connection_id"])
    is_owner = user_id == settings.owner_id
    is_subscribed = db.is_subscribed(user_id, settings.owner_id)
    has_trial = db.trial_available(user_id) if row else True

    photo_id = db.get_setting("broadcast_photo_id", "")
    caption = welcome_text(db)
    reply_kb = build_main_reply_keyboard(db)

    if photo_id:
        await bot.send_photo(user_id, photo=photo_id, caption=caption, parse_mode="HTML", reply_markup=reply_kb)
    else:
        await bot.send_message(user_id, caption, parse_mode="HTML", reply_markup=reply_kb)

    if not is_connected:
        me = await bot.get_me()
        await bot.send_message(
            user_id,
            build_not_connected_text(db),
            parse_mode="HTML",
            reply_markup=build_connect_keyboard(db, me.username),
        )
        return

    if not is_owner and not is_subscribed:
        await bot.send_message(
            user_id,
            "Чтобы включить функции бота, оформите подписку или возьмите пробный период.",
            reply_markup=build_welcome_keyboard(db, has_trial, is_subscribed),
        )
        return

    is_enabled = bool(row["enabled"])
    status_text = "<b>Время в нике включено.</b>" if is_enabled else "<b>Время в нике выключено.</b>"
    await bot.send_message(user_id, status_text, parse_mode="HTML", reply_markup=build_toggle_keyboard(db, is_enabled))


# ======================================================================
# USER HANDLERS
# ======================================================================


def register_user_handlers(
    dp: Dispatcher, db: Database, clock: NicknameClock, settings: Settings, crypto: CryptoBotClient
) -> None:
    @dp.message(Command("start"))
    async def handle_start(message: Message, command: CommandObject) -> None:
        user_id = message.from_user.id
        existing = db.get_user(user_id)
        db.upsert_user(user_id, message.from_user.first_name or "", message.from_user.username)
        db.register_start(user_id)

        # Реферальная ссылка вида t.me/<bot>?start=ref<id> — запоминаем
        # пригласившего только для НОВОГО пользователя (первый /start).
        if existing is None and command.args:
            args = command.args.strip()
            if args.startswith("ref"):
                try:
                    referrer_id = int(args[3:])
                except ValueError:
                    referrer_id = None
                if referrer_id and db.get_user(referrer_id):
                    db.set_referrer(user_id, referrer_id)

        if not await passes_channel_gate(message.bot, db, user_id):
            channel = db.get_setting("required_channel", "")
            await message.answer(
                "Для использования бота подпишитесь на канал, а затем нажмите «Я подписался».",
                reply_markup=build_channel_gate_keyboard(db, channel),
            )
            return

        await show_start_screen(message.bot, db, settings, user_id)

    @dp.callback_query(F.data == "check_channel")
    async def handle_check_channel(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        if await passes_channel_gate(callback.bot, db, user_id):
            await callback.answer("Спасибо! Доступ открыт.", show_alert=True)
            try:
                await callback.message.delete()
            except Exception:
                pass
            await show_start_screen(callback.bot, db, settings, user_id)
        else:
            await callback.answer("Вы ещё не подписались на канал.", show_alert=True)

    @dp.callback_query(F.data == "ref_info")
    async def handle_ref_info(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        me = await callback.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref{user_id}"
        count = db.count_referrals(user_id)
        text = (
            "<b>🔗 Реферальная программа</b>\n\n"
            f"Ваша ссылка:\n<code>{escape(link)}</code>\n\n"
            f"За каждого друга, который реально подключит бота в Business-автоматизацию, "
            f"вам начисляется <b>{REFERRAL_BONUS_DAYS} дня</b> подписки.\n\n"
            f"Приглашено (подключили бота): <b>{count}</b>"
        )
        await callback.message.answer(text, parse_mode="HTML", reply_markup=build_back_keyboard(db, "back_to_start"))
        await callback.answer()

    @dp.message(F.text == "Главная")
    async def handle_home_button(message: Message) -> None:
        db.upsert_user(message.from_user.id, message.from_user.first_name or "", message.from_user.username)
        await show_start_screen(message.bot, db, settings, message.from_user.id)

    @dp.callback_query(F.data == "back_to_start")
    async def handle_back_to_start(callback: CallbackQuery) -> None:
        await callback.message.delete()
        await show_start_screen(callback.bot, db, settings, callback.from_user.id)
        await callback.answer()

    @dp.callback_query(F.data == "show_instruction")
    async def handle_show_instruction(callback: CallbackQuery) -> None:
        await callback.message.answer(
            instruction_text(db), parse_mode="HTML", reply_markup=build_instruction_keyboard(db)
        )
        await callback.answer()

    @dp.callback_query(F.data == "show_support")
    async def handle_show_support(callback: CallbackQuery) -> None:
        await callback.message.answer(
            support_text(db), parse_mode="HTML", reply_markup=build_support_keyboard(db)
        )
        await callback.answer()

    @dp.callback_query(F.data == "close_info")
    async def handle_close_info(callback: CallbackQuery) -> None:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer()

    @dp.business_connection()
    async def handle_business_connection(connection: BusinessConnection) -> None:
        user_id = connection.user.id
        row_before = db.get_user(user_id)
        was_connected = bool(row_before and row_before["business_connection_id"])
        db.upsert_user(user_id, connection.user.first_name or "", connection.user.username)

        if connection.is_enabled:
            db.set_connection(user_id, connection.id)

            # Реферальный бонус начисляется строго за реальное подключение
            # Business Connection (не за простой /start по ссылке), и только
            # один раз — при первом подключении приглашённого.
            if not was_connected:
                referrer_id = db.credit_referral_if_needed(user_id, REFERRAL_BONUS_DAYS)
                if referrer_id:
                    try:
                        await connection.bot.send_message(
                            referrer_id,
                            f"🎉 Ваш друг подключил бота — начислено <b>{REFERRAL_BONUS_DAYS} дня</b> подписки!",
                            parse_mode="HTML",
                        )
                    except Exception:
                        logger.exception("Failed to notify referrer_id=%s about bonus", referrer_id)

            try:
                await connection.bot.send_message(
                    user_id, "<b>Бот подключён.</b>", parse_mode="HTML",
                    reply_markup=build_toggle_keyboard(db, False),
                )
            except Exception:
                logger.exception("Failed to send connection confirmation to user_id=%s", user_id)
            return

        row = db.get_user(user_id)
        if row and row["business_connection_id"]:
            await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")

        db.set_connection(user_id, None)
        db.set_enabled(user_id, False)

    # -- .mute / .spam: программная имитация мута собеседника в личном
    # бизнес-чате. У Telegram нет API для "мута" в приватном диалоге (это
    # функция только групп), поэтому реализуем через немедленное удаление
    # входящих сообщений собеседника, пока для этого чата стоит флаг мута.
    _SPAM_MAX_COUNT = 100  # защита от лимитов Telegram и случайных опечаток

    def _build_unmute_keyboard(chat_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [_btn(db, "unmute", unmute_button_text(db), callback_data=f"unmute:{chat_id}")]
            ]
        )

    @dp.business_message()
    async def handle_business_message(message: Message) -> None:
        connection_id = message.business_connection_id
        if not connection_id:
            return

        row = db.get_user_by_connection(connection_id)
        if not row:
            return
        owner_id = row["user_id"]
        chat_id = message.chat.id
        is_from_owner = message.from_user and message.from_user.id == owner_id

        # Сообщение от самого владельца бизнес-аккаунта — проверяем команды.
        if is_from_owner:
            text = (message.text or message.caption or "").strip()

            if text == ".mute":
                try:
                    await message.bot.delete_business_messages(
                        business_connection_id=connection_id, message_ids=[message.message_id]
                    )
                except Exception:
                    logger.exception("Failed to delete .mute command message, chat_id=%s", chat_id)
                db.mute_chat(owner_id, chat_id)
                try:
                    await message.bot.send_message(
                        chat_id=chat_id,
                        business_connection_id=connection_id,
                        text=mute_text(db),
                        reply_markup=_build_unmute_keyboard(chat_id),
                    )
                except Exception:
                    logger.exception("Failed to send mute notice, chat_id=%s", chat_id)
                return

            if text == ".xo":
                try:
                    await message.bot.delete_business_messages(
                        business_connection_id=connection_id, message_ids=[message.message_id]
                    )
                except Exception:
                    logger.exception("Failed to delete .xo command message, chat_id=%s", chat_id)
                db.xo_new_game(owner_id, chat_id)
                game_row = db.xo_get(owner_id, chat_id)
                try:
                    await message.bot.send_message(
                        chat_id=chat_id,
                        business_connection_id=connection_id,
                        text=render_emoji_tags(
                            db, _xo_status_text(db, game_row, "Владелец", "Собеседник")
                        ),
                        parse_mode="HTML",
                        reply_markup=_xo_board_keyboard(db, chat_id, game_row),
                    )
                except Exception:
                    logger.exception("Failed to send xo game, chat_id=%s", chat_id)
                return

            if text.startswith(".spam"):
                parts = text.split(maxsplit=2)
                # Формат: .spam 20 Привет
                if len(parts) >= 3 and parts[1].isdigit() and int(parts[1]) > 0:
                    count = min(int(parts[1]), _SPAM_MAX_COUNT)
                    spam_text = parts[2]
                    try:
                        await message.bot.delete_business_messages(
                            business_connection_id=connection_id, message_ids=[message.message_id]
                        )
                    except Exception:
                        logger.exception("Failed to delete .spam command message, chat_id=%s", chat_id)
                    for _ in range(count):
                        try:
                            await message.bot.send_message(
                                chat_id=chat_id,
                                business_connection_id=connection_id,
                                text=spam_text,
                            )
                        except Exception:
                            logger.exception("Failed to send spam message, chat_id=%s", chat_id)
                            break
                        # Небольшая пауза, чтобы не словить flood control Telegram.
                        await asyncio.sleep(0.15)
                    return
                else:
                    try:
                        await message.bot.delete_business_messages(
                            business_connection_id=connection_id, message_ids=[message.message_id]
                        )
                    except Exception:
                        pass
                    try:
                        await message.bot.send_message(
                            chat_id=chat_id,
                            business_connection_id=connection_id,
                            text="Формат команды: .spam 20 Привет",
                        )
                    except Exception:
                        logger.exception("Failed to send spam usage hint, chat_id=%s", chat_id)
                    return

            return

        # Сообщение от собеседника — удаляем его, если этот чат замучен.
        if db.is_chat_muted(owner_id, chat_id):
            try:
                await message.bot.delete_business_messages(
                    business_connection_id=connection_id, message_ids=[message.message_id]
                )
            except Exception:
                logger.exception(
                    "Failed to delete muted interlocutor message, chat_id=%s owner_id=%s", chat_id, owner_id
                )

    def _resolve_business_owner(callback: CallbackQuery) -> Optional[sqlite3.Row]:
        """Определяет владельца бизнес-аккаунта, к которому относится чат
        колбэка — НЕ по тому, кто нажал кнопку (это может быть и собеседник
        владельца), а по business_connection_id колбэка, либо (фолбэк) по
        тому, что нажавший сам подключён своим бизнес-аккаунтом."""
        connection_id = getattr(callback, "business_connection_id", None)
        owner_row = db.get_user_by_connection(connection_id) if connection_id else None
        if owner_row is None:
            candidate = db.get_user(callback.from_user.id)
            if candidate and candidate["business_connection_id"]:
                owner_row = candidate
        if not owner_row or not owner_row["business_connection_id"]:
            return None
        return owner_row

    @dp.callback_query(F.data.startswith("unmute:"))
    async def handle_unmute(callback: CallbackQuery) -> None:
        chat_id = int(callback.data.split(":", 1)[1])

        # Кнопка «Размутить» лежит на сообщении в личном чате между владельцем
        # бизнес-аккаунта и собеседником — нажать её физически может любой из
        # двух участников этого чата, поэтому владельца определяем не по
        # тому, кто нажал.
        owner_row = _resolve_business_owner(callback)
        if not owner_row:
            await callback.answer("Бот не подключён", show_alert=True)
            return

        owner_id = owner_row["user_id"]
        if callback.from_user.id != owner_id:
            # Кнопку нажал не владелец бизнес-аккаунта (например, тот, кого
            # замутили) — игнорируем, размутить может только владелец.
            await callback.answer("Размутить может только владелец аккаунта.", show_alert=True)
            return

        db.unmute_chat(owner_id, chat_id)
        try:
            await callback.message.edit_text("Собеседник размучен.", reply_markup=None)
        except Exception:
            pass
        await callback.answer("Размучено")

    @dp.callback_query(F.data.startswith("xo_mv:"))
    async def handle_xo_move(callback: CallbackQuery) -> None:
        _, chat_id_s, idx_s = callback.data.split(":", 2)
        chat_id = int(chat_id_s)
        idx = int(idx_s)

        owner_row = _resolve_business_owner(callback)
        if not owner_row:
            await callback.answer("Бот не подключён", show_alert=True)
            return
        owner_id = owner_row["user_id"]

        game_row = db.xo_get(owner_id, chat_id)
        if not game_row or game_row["status"] != "active":
            await callback.answer("Игра не активна. Начните новую: .xo", show_alert=True)
            return

        # Владелец всегда играет X, второй участник чата — O.
        clicker_symbol = "X" if callback.from_user.id == owner_id else "O"
        if clicker_symbol != game_row["turn"]:
            await callback.answer("Сейчас не ваш ход.", show_alert=True)
            return

        board = list(game_row["board"])
        if board[idx] != ".":
            await callback.answer("Клетка уже занята.", show_alert=True)
            return

        board[idx] = clicker_symbol
        board_str = "".join(board)
        winner = _xo_check_winner(board_str)
        if winner:
            status = f"won_{winner}"
        elif _xo_is_full(board_str):
            status = "draw"
        else:
            status = "active"
        next_turn = "O" if game_row["turn"] == "X" else "X"
        db.xo_save(owner_id, chat_id, board_str, next_turn, status)
        game_row = db.xo_get(owner_id, chat_id)

        try:
            await callback.message.edit_text(
                render_emoji_tags(db, _xo_status_text(db, game_row, "Владелец", "Собеседник")),
                parse_mode="HTML",
                reply_markup=_xo_board_keyboard(db, chat_id, game_row),
            )
        except Exception:
            logger.exception("Failed to update xo board, chat_id=%s", chat_id)
        await callback.answer()

    @dp.callback_query(F.data.startswith("xo_new:"))
    async def handle_xo_new(callback: CallbackQuery) -> None:
        chat_id = int(callback.data.split(":", 1)[1])
        owner_row = _resolve_business_owner(callback)
        if not owner_row:
            await callback.answer("Бот не подключён", show_alert=True)
            return
        owner_id = owner_row["user_id"]
        if callback.from_user.id != owner_id:
            # Новую игру может начать только владелец аккаунта (как и .xo).
            await callback.answer("Начать новую игру может только владелец аккаунта.", show_alert=True)
            return
        db.xo_new_game(owner_id, chat_id)
        game_row = db.xo_get(owner_id, chat_id)
        try:
            await callback.message.edit_text(
                render_emoji_tags(db, _xo_status_text(db, game_row, "Владелец", "Собеседник")),
                parse_mode="HTML",
                reply_markup=_xo_board_keyboard(db, chat_id, game_row),
            )
        except Exception:
            logger.exception("Failed to restart xo game, chat_id=%s", chat_id)
        await callback.answer("Новая игра!")

    @dp.callback_query(F.data == "toggle_on")
    async def handle_toggle_on(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        row = db.get_user(user_id)
        if not row or not row["business_connection_id"]:
            await callback.answer("Бот не подключён", show_alert=True)
            return
        if user_id != settings.owner_id and not db.is_subscribed(user_id, settings.owner_id):
            await callback.answer("Нужна активная подписка", show_alert=True)
            return
        db.set_enabled(user_id, True)
        await clock.apply(db.get_user(user_id))
        await callback.message.edit_text(
            "<b>Время в нике включено.</b>", parse_mode="HTML", reply_markup=build_toggle_keyboard(db, True)
        )
        await callback.answer()

    @dp.callback_query(F.data == "toggle_off")
    async def handle_toggle_off(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        row = db.get_user(user_id)
        if not row:
            await callback.answer()
            return
        db.set_enabled(user_id, False)
        if row["business_connection_id"]:
            await clock.clear(user_id, row["business_connection_id"], row["first_name"] or "")
        await callback.message.edit_text(
            "<b>Время в нике выключено.</b>", parse_mode="HTML", reply_markup=build_toggle_keyboard(db, False)
        )
        await callback.answer()

    @dp.callback_query(F.data == "open_settings")
    async def handle_open_settings(callback: CallbackQuery) -> None:
        row = db.get_user(callback.from_user.id)
        await callback.message.edit_text(
            "<b>⚙️ Настройки формата ника</b>\n\nВыберите режим отображения:",
            parse_mode="HTML", reply_markup=build_settings_keyboard(db, row),
        )
        await callback.answer()

    @dp.callback_query(F.data == "mode_time")
    async def handle_mode_time(callback: CallbackQuery) -> None:
        db.set_mode(callback.from_user.id, "time")
        row = db.get_user(callback.from_user.id)
        await callback.message.edit_reply_markup(reply_markup=build_settings_keyboard(db, row))
        await callback.answer("Режим: Время (ЧЧ:ММ)")

    @dp.callback_query(F.data == "mode_seconds")
    async def handle_mode_seconds(callback: CallbackQuery) -> None:
        db.set_mode(callback.from_user.id, "seconds")
        row = db.get_user(callback.from_user.id)
        await callback.message.edit_reply_markup(reply_markup=build_settings_keyboard(db, row))
        await callback.answer(
            "Режим: Время с секундами. Ник обновляется примерно раз в 12 секунд "
            "(частые обновления упираются в лимиты Telegram Business API).", show_alert=True,
        )

    @dp.callback_query(F.data == "mode_date")
    async def handle_mode_date(callback: CallbackQuery) -> None:
        db.set_mode(callback.from_user.id, "date")
        row = db.get_user(callback.from_user.id)
        await callback.message.edit_reply_markup(reply_markup=build_settings_keyboard(db, row))
        await callback.answer("Режим: Дата")

    @dp.callback_query(F.data == "mode_countdown")
    async def handle_mode_countdown(callback: CallbackQuery, state: FSMContext) -> None:
        db.set_mode(callback.from_user.id, "countdown")
        await state.set_state(UserStates.waiting_countdown_target)
        await callback.message.answer(
            "Введите дату и (опционально) название события в формате:\n"
            "<code>ГГГГ-ММ-ДД Название</code>\n\nНапример: <code>2027-01-01 Новый год</code>",
            parse_mode="HTML",
        )
        await callback.answer()

    @dp.message(UserStates.waiting_countdown_target)
    async def handle_countdown_input(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        parts = text.split(maxsplit=1)
        date_part = parts[0] if parts else ""
        label = parts[1] if len(parts) > 1 else "Отсчёт"
        try:
            target = datetime.strptime(date_part, "%Y-%m-%d")
        except ValueError:
            await message.answer("Не удалось распознать дату. Формат: <code>ГГГГ-ММ-ДД Название</code>", parse_mode="HTML")
            return
        db.set_countdown(message.from_user.id, target.isoformat(), label)
        await state.clear()
        await message.answer(f"✅ Обратный отсчёт до «{label}» ({date_part}) установлен.")
        row = db.get_user(message.from_user.id)
        await message.answer("<b>⚙️ Настройки формата ника</b>", parse_mode="HTML", reply_markup=build_settings_keyboard(db, row))

    # -- subscription / payments -----------------------------------------
    @dp.callback_query(F.data == "use_trial")
    async def handle_use_trial(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        if not db.trial_available(user_id):
            await callback.answer("Пробный период уже был использован", show_alert=True)
            return
        days = db.get_trial_days()
        db.use_trial(user_id, days)
        await callback.answer("Пробный период активирован!", show_alert=True)
        await callback.message.delete()
        await show_start_screen(callback.bot, db, settings, user_id)

    @dp.callback_query(F.data == "pay_stars")
    async def handle_pay_stars(callback: CallbackQuery) -> None:
        price = db.get_price_stars()
        oferta_url = db.get_setting("oferta_url", "")
        description = f"Доступ ко всем функциям бота Time на 30 дней ({price}⭐️)."
        if oferta_url:
            await callback.message.answer(
                "Оплачивая подписку, вы принимаете условия "
                f'<a href="{escape(oferta_url)}">публичной оферты</a>. '
                "Оплата означает согласие с условиями и невозможность возврата средств.",
                parse_mode="HTML",
            )
            description += " Оплата означает согласие с публичной офертой, средства не возвращаются."
        await callback.bot.send_invoice(
            chat_id=callback.from_user.id,
            title="Подписка Time — 1 месяц",
            description=description,
            payload=f"sub_month_stars:{price}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Подписка на 1 месяц", amount=price)],
        )
        await callback.answer()

    @dp.callback_query(F.data == "pay_crypto")
    async def handle_pay_crypto(callback: CallbackQuery) -> None:
        if not crypto.configured:
            await callback.answer("Оплата криптой временно недоступна", show_alert=True)
            return
        price = db.get_price_usdt()
        user_id = callback.from_user.id
        oferta_url = db.get_setting("oferta_url", "")
        try:
            invoice = await crypto.create_invoice(
                amount=price, description="Подписка Time — 1 месяц", payload=f"sub_month:{user_id}"
            )
        except CryptoPayError as exc:
            logger.exception("CryptoBot invoice creation failed")
            await callback.answer(f"Ошибка создания счёта: {exc}", show_alert=True)
            return

        db.save_crypto_invoice(str(invoice["invoice_id"]), user_id)
        pay_url = invoice.get("bot_invoice_url") or invoice.get("pay_url")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=f"Оплатить {price}$ USDT", url=pay_url, style="success")]]
        )
        note = "Счёт на оплату создан. После оплаты подписка активируется автоматически в течение ~10 секунд."
        if oferta_url:
            note += (
                f'\n\nОплачивая счёт, вы принимаете условия <a href="{escape(oferta_url)}">'
                "публичной оферты</a>. Средства не возвращаются."
            )
        await callback.message.answer(note, parse_mode="HTML", reply_markup=kb)
        await callback.answer()

    @dp.pre_checkout_query()
    async def handle_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
        await pre_checkout_query.answer(ok=True)

    @dp.message(F.successful_payment)
    async def handle_successful_payment(message: Message) -> None:
        payment = message.successful_payment
        stars = payment.total_amount
        db.record_payment(message.from_user.id, "stars", str(stars), payment.telegram_payment_charge_id)
        new_until = db.grant_subscription(message.from_user.id, 30)
        until_str = datetime.fromtimestamp(new_until).strftime("%d.%m.%Y")
        await message.answer(
            f"✅ <b>Оплата прошла успешно!</b>\nПодписка активна до <b>{until_str}</b>.", parse_mode="HTML"
        )
        await show_start_screen(message.bot, db, settings, message.from_user.id)


# ======================================================================
# ADMIN HANDLERS
# ======================================================================


def _is_owner(user_id: int, settings: Settings) -> bool:
    return user_id == settings.owner_id


def register_admin_handlers(dp: Dispatcher, db: Database, settings: Settings) -> None:
    @dp.message(Command("admin"))
    async def handle_admin_entry(message: Message) -> None:
        if not _is_owner(message.from_user.id, settings):
            return
        await message.answer("<b>🔐 Секретная админ-панель</b>", parse_mode="HTML", reply_markup=build_admin_menu_keyboard(db))

    @dp.callback_query(F.data == "admin_menu")
    async def handle_admin_menu(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await callback.message.edit_text("<b>🔐 Секретная админ-панель</b>", parse_mode="HTML", reply_markup=build_admin_menu_keyboard(db))
        await callback.answer()

    @dp.callback_query(F.data == "admin_price")
    async def handle_admin_price(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        current_stars = db.get_price_stars()
        current_usdt = db.get_price_usdt()
        await state.set_state(AdminStates.waiting_price)
        await callback.message.edit_text(
            f"Текущая цена: <b>{current_stars}⭐️</b> / <b>{current_usdt}$</b>\n"
            "Введите новую цену в stars (число):",
            parse_mode="HTML", reply_markup=build_back_keyboard(db),
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_price)
    async def handle_price_input(message: Message, state: FSMContext) -> None:
        try:
            value = int((message.text or "").strip())
            if value <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Введите положительное целое число.")
            return
        db.set_setting("price_stars", str(value))
        await state.set_state(AdminStates.waiting_price_usdt)
        await message.answer("Теперь введите цену в USDT (например 0.5):")

    @dp.message(AdminStates.waiting_price_usdt)
    async def handle_price_usdt_input(message: Message, state: FSMContext) -> None:
        try:
            value = float((message.text or "").strip().replace(",", "."))
            if value <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Введите положительное число, например 0.5.")
            return
        db.set_setting("price_usdt", str(value))
        await state.clear()
        await message.answer(
            f"✅ Новая цена: {db.get_price_stars()}⭐️ / {value}$ USDT", reply_markup=build_back_keyboard(db)
        )

    @dp.callback_query(F.data == "admin_trial")
    async def handle_admin_trial(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        current = db.get_trial_days()
        await state.set_state(AdminStates.waiting_trial_days)
        await callback.message.edit_text(
            f"Текущий пробный период: <b>{current} дн.</b>\nВведите новое число дней:",
            parse_mode="HTML", reply_markup=build_back_keyboard(db),
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_trial_days)
    async def handle_trial_input(message: Message, state: FSMContext) -> None:
        try:
            value = int((message.text or "").strip())
            if value <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Введите положительное целое число дней.")
            return
        db.set_setting("trial_days", str(value))
        await state.clear()
        await message.answer(f"✅ Пробный период теперь: {value} дн.", reply_markup=build_back_keyboard(db))

    @dp.callback_query(F.data == "admin_grant")
    async def handle_admin_grant(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_grant_id)
        await callback.message.edit_text("Введите user_id пользователя, которому хотите выдать подписку:", reply_markup=build_back_keyboard(db))
        await callback.answer()

    @dp.message(AdminStates.waiting_grant_id)
    async def handle_grant_id_input(message: Message, state: FSMContext) -> None:
        try:
            uid = int((message.text or "").strip())
        except ValueError:
            await message.answer("Введите числовой user_id.")
            return
        await state.update_data(grant_uid=uid)
        await state.set_state(AdminStates.waiting_grant_days)
        await message.answer("На сколько дней выдать подписку?")

    @dp.message(AdminStates.waiting_grant_days)
    async def handle_grant_days_input(message: Message, state: FSMContext) -> None:
        try:
            days = int((message.text or "").strip())
            if days <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Введите положительное целое число дней.")
            return
        data = await state.get_data()
        uid = data["grant_uid"]
        new_until = db.grant_subscription(uid, days)
        until_str = datetime.fromtimestamp(new_until).strftime("%d.%m.%Y")
        await state.clear()
        await message.answer(f"✅ Пользователю <code>{uid}</code> выдана подписка до {until_str}.", parse_mode="HTML", reply_markup=build_back_keyboard(db))
        try:
            await message.bot.send_message(uid, f"🎁 Вам выдана подписка на {days} дн.! Действует до {until_str}.")
        except Exception:
            logger.exception("Failed to notify user_id=%s about granted subscription", uid)

    @dp.callback_query(F.data == "admin_revoke")
    async def handle_admin_revoke(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_revoke_id)
        await callback.message.edit_text("Введите user_id пользователя, у которого нужно отобрать подписку:", reply_markup=build_back_keyboard(db))
        await callback.answer()

    @dp.message(AdminStates.waiting_revoke_id)
    async def handle_revoke_id_input(message: Message, state: FSMContext) -> None:
        try:
            uid = int((message.text or "").strip())
        except ValueError:
            await message.answer("Введите числовой user_id.")
            return
        db.revoke_subscription(uid)
        await state.clear()
        await message.answer(f"✅ Подписка пользователя <code>{uid}</code> отозвана.", parse_mode="HTML", reply_markup=build_back_keyboard(db))

    @dp.callback_query(F.data == "admin_broadcast")
    async def handle_admin_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_broadcast_text)
        await callback.message.edit_text("Введите текст рассылки (можно с HTML-разметкой):", reply_markup=build_back_keyboard(db))
        await callback.answer()

    @dp.message(AdminStates.waiting_broadcast_text)
    async def handle_broadcast_text_input(message: Message, state: FSMContext) -> None:
        await state.update_data(broadcast_text=message.html_text)
        await state.set_state(AdminStates.waiting_broadcast_photo)
        await message.answer("Отправьте фото для рассылки, либо напишите «нет», чтобы отправить только текст.")

    @dp.message(AdminStates.waiting_broadcast_photo)
    async def handle_broadcast_photo_input(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        text = data.get("broadcast_text", "")
        photo_id = None
        if message.photo:
            photo_id = message.photo[-1].file_id
        elif (message.text or "").strip().lower() not in ("нет", "no", "-"):
            await message.answer("Отправьте фото или напишите «нет».")
            return

        await state.clear()
        users = db.get_all_users()
        await message.answer(f"Рассылка запущена на {len(users)} пользователей...")

        sent, failed = 0, 0
        for row in users:
            try:
                if photo_id:
                    await message.bot.send_photo(row["user_id"], photo=photo_id, caption=text, parse_mode="HTML")
                else:
                    await message.bot.send_message(row["user_id"], text, parse_mode="HTML")
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)

        await message.answer(f"✅ Рассылка завершена. Отправлено: {sent}, ошибок: {failed}.", reply_markup=build_back_keyboard(db))

    @dp.callback_query(F.data == "admin_photo")
    async def handle_admin_photo(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await state.set_state(AdminStates.waiting_photo)
        await callback.message.edit_text(
            "Отправьте фото, которое будет показываться в приветствии (/start). "
            "Напишите «удалить», чтобы убрать текущее фото.",
            reply_markup=build_back_keyboard(db),
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_photo)
    async def handle_photo_input(message: Message, state: FSMContext) -> None:
        if message.photo:
            photo_id = message.photo[-1].file_id
            db.set_setting("broadcast_photo_id", photo_id)
            await state.clear()
            await message.answer("✅ Фото приветствия обновлено.", reply_markup=build_back_keyboard(db))
            return
        if (message.text or "").strip().lower() in ("удалить", "delete", "-"):
            db.set_setting("broadcast_photo_id", "")
            await state.clear()
            await message.answer("✅ Фото приветствия удалено.", reply_markup=build_back_keyboard(db))
            return
        await message.answer("Отправьте фото или напишите «удалить».")

    def _emoji_pick_keys() -> list[tuple[str, str]]:
        return TEXT_EMOJI_KEYS + [(f"btn_{k}", label) for k, label in STYLE_KEYS]

    def _grid_styled(items: list[tuple[str, str]], prefix: str, selected: set[str]) -> list[list[InlineKeyboardButton]]:
        """Как _grid, но вместо текстовой метки подсвечивает уже заданные
        ключи цветом кнопки (success), без эмодзи в тексте."""
        rows: list[list[InlineKeyboardButton]] = []
        pair: list[InlineKeyboardButton] = []
        for key, label in items:
            kwargs = {"callback_data": f"{prefix}:{key}"}
            if key in selected:
                kwargs["style"] = "success"
            btn = InlineKeyboardButton(text=label, **kwargs)
            pair.append(btn)
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
        return rows

    @dp.callback_query(F.data == "admin_emoji")
    async def handle_admin_emoji(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        set_keys = {r["key"] for r in db.get_all_emoji() if r["emoji_id"]}
        kb_rows = _grid_styled(_emoji_pick_keys(), "pick_emoji", set_keys)
        kb_rows.append([InlineKeyboardButton(text=f"Каталог пака ({len(EMOJI_PACK)} шт.)", callback_data="pack_page:0")])
        kb_rows.append([_btn(db, "back", "Назад", callback_data="admin_menu")])
        await callback.message.edit_text(
            "<b>Премиум-эмодзи</b>\n\n"
            "Нажмите на кнопку, для которой хотите поставить эмодзи (подсвечена — уже задано), "
            "затем пришлите нужный премиум-эмодзи одним сообщением.\n\n"
            "Ключи из этого списка можно также вставлять в любой настраиваемый текст бота "
            "плейсхолдером <code>{emoji:ключ}</code>, например <code>{emoji:star}</code>.\n\n"
            f"Также загружен готовый каталог из {len(EMOJI_PACK)} премиум-эмодзи — "
            "их можно сразу вставлять в любой текст без ручной загрузки.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        )
        await callback.answer()

    _PACK_PAGE_SIZE = 12

    def _build_pack_page_keyboard(page: int) -> InlineKeyboardMarkup:
        total = len(EMOJI_PACK)
        pages_count = (total + _PACK_PAGE_SIZE - 1) // _PACK_PAGE_SIZE
        page = max(0, min(page, pages_count - 1))
        start = page * _PACK_PAGE_SIZE
        chunk = EMOJI_PACK[start:start + _PACK_PAGE_SIZE]

        rows: list[list[InlineKeyboardButton]] = []
        pair: list[InlineKeyboardButton] = []
        for key, fallback, emoji_id in chunk:
            btn = InlineKeyboardButton(
                text=fallback,
                callback_data=f"pack_view:{key}:{page}",
                icon_custom_emoji_id=emoji_id,
            )
            pair.append(btn)
            if len(pair) == 4:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)

        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="←", callback_data=f"pack_page:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages_count}", callback_data="noop"))
        if page < pages_count - 1:
            nav.append(InlineKeyboardButton(text="→", callback_data=f"pack_page:{page + 1}"))
        rows.append(nav)
        rows.append([_btn(db, "back", "Назад", callback_data="admin_emoji")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @dp.callback_query(F.data.startswith("pack_page:"))
    async def handle_pack_page(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        page = int(callback.data.split(":", 1)[1])
        await callback.message.edit_text(
            "<b>Каталог премиум-эмодзи</b>\n\n"
            "Нажмите на эмодзи, чтобы получить код для вставки в текст.",
            parse_mode="HTML",
            reply_markup=_build_pack_page_keyboard(page),
        )
        await callback.answer()

    @dp.callback_query(F.data == "noop")
    async def handle_noop(callback: CallbackQuery) -> None:
        await callback.answer()

    @dp.callback_query(F.data.startswith("pack_view:"))
    async def handle_pack_view(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        _, key, page = callback.data.split(":", 2)
        code = "{emoji:" + key + "}"
        pair = db.get_emoji_full(key)
        preview_row = []
        if pair:
            emoji_id, fallback = pair
            preview_row = [InlineKeyboardButton(text=f"Это оно →  {fallback}", callback_data="noop", icon_custom_emoji_id=emoji_id)]
        kb_rows = []
        if preview_row:
            kb_rows.append(preview_row)
        kb_rows.append([InlineKeyboardButton(text="Скопировать код", copy_text=CopyTextButton(text=code))])
        kb_rows.append([InlineKeyboardButton(text="Назад к каталогу", callback_data=f"pack_page:{page}")])
        await callback.message.edit_text(
            f"Код для вставки в любой текст:\n<code>{escape(code)}</code>\n\n"
            "Скопируйте кнопкой ниже и вставьте в нужном месте текста "
            "(раздел «Тексты бота»).",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("pick_emoji:"))
    async def handle_pick_emoji(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        label = dict(_emoji_pick_keys()).get(key, key)
        await state.set_state(AdminStates.waiting_emoji_value)
        await state.update_data(emoji_key=key)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"📦 Выбрать из каталога ({len(EMOJI_PACK)} шт.)", callback_data=f"pack_pick:{key}:0")],
                [_btn(db, "back", "Назад", callback_data="admin_emoji")],
            ]
        )
        await callback.message.edit_text(
            f"Выбрано: <b>{label}</b>\n\n"
            "Проще всего — нажмите «Выбрать из каталога» и тапните готовый эмодзи из "
            f"загруженного пака ({len(EMOJI_PACK)} шт.) — он назначится сразу, по ID, без лишних шагов.\n\n"
            "Либо пришлите свой премиум-эмодзи одним сообщением (просто отправьте его как текст) — "
            "так бот сам определит правильный символ-носитель.",
            parse_mode="HTML",
            reply_markup=kb,
        )
        await callback.answer()

    _PACK_PICK_PAGE_SIZE = 12

    def _build_pack_pick_keyboard(target_key: str, page: int) -> InlineKeyboardMarkup:
        total = len(EMOJI_PACK)
        pages_count = (total + _PACK_PICK_PAGE_SIZE - 1) // _PACK_PICK_PAGE_SIZE
        page = max(0, min(page, pages_count - 1))
        start = page * _PACK_PICK_PAGE_SIZE
        chunk = EMOJI_PACK[start:start + _PACK_PICK_PAGE_SIZE]

        rows: list[list[InlineKeyboardButton]] = []
        pair: list[InlineKeyboardButton] = []
        for pack_key, fallback, emoji_id in chunk:
            btn = InlineKeyboardButton(
                text=fallback,
                callback_data=f"pack_apply:{target_key}:{pack_key}:{page}",
                icon_custom_emoji_id=emoji_id,
            )
            pair.append(btn)
            if len(pair) == 4:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)

        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="←", callback_data=f"pack_pick:{target_key}:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages_count}", callback_data="noop"))
        if page < pages_count - 1:
            nav.append(InlineKeyboardButton(text="→", callback_data=f"pack_pick:{target_key}:{page + 1}"))
        rows.append(nav)
        rows.append([_btn(db, "back", "Назад", callback_data=f"pick_emoji:{target_key}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @dp.callback_query(F.data.startswith("pack_pick:"))
    async def handle_pack_pick(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        _, target_key, page_s = callback.data.split(":", 2)
        await callback.message.edit_text(
            "Тапните эмодзи, чтобы назначить его выбранной кнопке/тексту.",
            reply_markup=_build_pack_pick_keyboard(target_key, int(page_s)),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("pack_apply:"))
    async def handle_pack_apply(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        _, target_key, pack_key, _page_s = callback.data.split(":", 3)
        pair = EMOJI_PACK_MAP.get(pack_key)
        if not pair:
            await callback.answer("Эмодзи не найдено в паке.", show_alert=True)
            return
        fallback, emoji_id = pair
        db.set_emoji(target_key, emoji_id, fallback)
        await state.clear()
        label = dict(_emoji_pick_keys()).get(target_key, target_key)
        await callback.message.edit_text(
            f"✅ Эмодзи назначено: <b>{label}</b>.",
            parse_mode="HTML",
            reply_markup=build_back_keyboard(db, "admin_emoji"),
        )
        await callback.answer("Готово")

    @dp.message(AdminStates.waiting_emoji_value)
    async def handle_emoji_value_input(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        key = data.get("emoji_key")
        if not key:
            await state.clear()
            return
        custom = next(
            (e for e in (message.entities or []) if e.type == "custom_emoji"), None
        )
        if custom and custom.custom_emoji_id:
            emoji_id = custom.custom_emoji_id
            # Достаём РЕАЛЬНЫЙ видимый символ-носитель из текста сообщения по
            # границам entity — именно на нём Telegram зарегистрировал этот
            # custom_emoji_id. Использование другого символа-заполнителя
            # (например "⭐" для всех) ломает <tg-emoji> с ENTITY_TEXT_INVALID.
            raw_text = message.text or ""
            # entity offset/length считаются в UTF-16 code units, как и Python str
            # для BMP-символов это совпадает; для суррогатных пар (многие эмодзи)
            # используем срез по UTF-16, чтобы не обрезать составной эмодзи.
            utf16 = raw_text.encode("utf-16-le")
            start = custom.offset * 2
            end = (custom.offset + custom.length) * 2
            fallback = utf16[start:end].decode("utf-16-le") or "⭐"
        else:
            text = (message.text or "").strip()
            if text.isdigit():
                emoji_id = text
                fallback = "⭐"
            else:
                await message.answer(
                    "Не распознал премиум-эмодзи. Пришлите его сообщением ещё раз, "
                    "либо отправьте числовой ID эмодзи."
                )
                return
        db.set_emoji(key, emoji_id, fallback)
        await state.clear()
        await message.answer(
            "Эмодзи обновлено.", reply_markup=build_back_keyboard(db, "admin_emoji")
        )

    def _grid_by_real_style(items: list[tuple[str, str]], prefix: str) -> list[list[InlineKeyboardButton]]:
        """Список кнопок пикера цвета отображается в РЕАЛЬНОМ цвете каждой
        кнопки (через style=...), а не через эмодзи-метку в тексте."""
        rows: list[list[InlineKeyboardButton]] = []
        pair: list[InlineKeyboardButton] = []
        for key, label in items:
            style = db.get_button_style(key)
            kwargs = {"callback_data": f"{prefix}:{key}"}
            if style:
                kwargs["style"] = style
            btn = InlineKeyboardButton(text=label, **kwargs)
            pair.append(btn)
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
        return rows

    @dp.callback_query(F.data == "admin_style")
    async def handle_admin_style(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        kb_rows = _grid_by_real_style(STYLE_KEYS, "pick_style")
        kb_rows.append([_btn(db, "back", "Назад", callback_data="admin_menu")])
        await callback.message.edit_text(
            "<b>Цвет inline-кнопок</b>\n\n"
            "Bot API 9.4 (с февраля 2026): primary / success / danger.\n"
            "Кнопки ниже уже показаны в своём текущем цвете. "
            "Нажмите на кнопку, чтобы изменить для неё цвет.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("pick_style:"))
    async def handle_pick_style(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        key = callback.data.split(":", 1)[1]
        label = dict(STYLE_KEYS).get(key, key)
        current = db.get_button_style(key) or "нет"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Primary", callback_data=f"set_style:{key}:primary", style="primary"),
                    InlineKeyboardButton(text="Success", callback_data=f"set_style:{key}:success", style="success"),
                ],
                [
                    InlineKeyboardButton(text="Danger", callback_data=f"set_style:{key}:danger", style="danger"),
                    InlineKeyboardButton(text="Сбросить", callback_data=f"set_style:{key}:none"),
                ],
                [_btn(db, "back", "Назад", callback_data="admin_style")],
            ]
        )
        await callback.message.edit_text(
            f"Кнопка: <b>{label}</b>\nТекущий цвет: <b>{current}</b>\n\nВыберите новый цвет:",
            parse_mode="HTML",
            reply_markup=kb,
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("set_style:"))
    async def handle_set_style(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        _, key, color = callback.data.split(":", 2)
        db.set_button_style(key, None if color == "none" else color)
        await callback.answer(f"Цвет обновлён: {color}", show_alert=False)
        # Перерисовываем список пикера — цвет кнопок в списке обновится сам.
        kb_rows = _grid_by_real_style(STYLE_KEYS, "pick_style")
        kb_rows.append([_btn(db, "back", "Назад", callback_data="admin_menu")])
        await callback.message.edit_text(
            "<b>Цвет inline-кнопок</b>\n\n"
            "Bot API 9.4 (с февраля 2026): primary / success / danger.\n"
            "Кнопки ниже уже показаны в своём текущем цвете. "
            "Нажмите на кнопку, чтобы изменить для неё цвет.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        )

    @dp.callback_query(F.data == "admin_oferta")
    async def handle_admin_oferta(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        current = db.get_setting("oferta_url", "")
        await state.set_state(AdminStates.waiting_oferta_url)
        text = (
            "<b>📄 Ссылка на публичную оферту</b>\n\n"
            f"Текущая ссылка: {escape(current) if current else '<i>не задана</i>'}\n\n"
            "Пришлите ссылку на Telegraph (или любую другую) со страницей оферты. "
            "Кнопка «Публичная оферта» появится на главном экране автоматически, "
            "как только ссылка будет задана.\n\n"
            "Напишите «удалить», чтобы убрать кнопку."
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=build_back_keyboard(db))
        await callback.answer()

    @dp.message(AdminStates.waiting_oferta_url)
    async def handle_oferta_url_input(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if text.lower() in ("удалить", "delete", "-"):
            db.set_setting("oferta_url", "")
            await state.clear()
            await message.answer("✅ Ссылка на оферту удалена, кнопка скрыта.", reply_markup=build_back_keyboard(db))
            return
        if not (text.startswith("http://") or text.startswith("https://")):
            await message.answer("Ссылка должна начинаться с http:// или https://. Попробуйте ещё раз.")
            return
        db.set_setting("oferta_url", text)
        await state.clear()
        await message.answer("✅ Ссылка на оферту сохранена. Кнопка появится на главном экране.", reply_markup=build_back_keyboard(db))

    # -- Тексты бота: любой шаблон можно переписать целиком, вставляя
    # {emoji:ключ} в любом месте — не только приветствие. -------------------
    _TEXT_TEMPLATE_KEYS: list[tuple[str, str, str]] = [
        # (settings_key, label, поддерживаемые {переменные})
        ("welcome_template", "Приветствие", "{trial_days} {trial_word} {price_stars} {price_usdt}"),
        ("instruction_template", "Инструкция", "{trial_days} {trial_word} {price_stars} {price_usdt}"),
        ("support_template", "Поддержка", "—"),
        ("not_connected_template", "Экран «не подключён»", "—"),
        ("mute_template", "Текст «Помолчи» (после .mute)", "—"),
        ("unmute_button_text", "Текст кнопки «Размутить» (без HTML/эмодзи-тегов)", "—"),
    ]

    def _build_texts_menu_keyboard() -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for settings_key, label, _vars in _TEXT_TEMPLATE_KEYS:
            is_custom = bool(db.get_setting(settings_key, ""))
            kwargs = {"callback_data": f"pick_text:{settings_key}"}
            if is_custom:
                kwargs["style"] = "success"
            rows.append([InlineKeyboardButton(text=label, **kwargs)])
        rows.append([_btn(db, "back", "Назад", callback_data="admin_menu")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @dp.callback_query(F.data == "admin_texts")
    async def handle_admin_texts(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        await callback.message.edit_text(
            "<b>Тексты бота</b>\n\n"
            "Выберите текст для редактирования. Подсвеченные — уже переопределены вручную.\n\n"
            "В любой текст можно вставить премиум-эмодзи плейсхолдером "
            "<code>{emoji:ключ}</code> (ключи заданы в разделе «Премиум-эмодзи»), "
            "не только в приветствие — в любом месте любого текста.",
            parse_mode="HTML",
            reply_markup=_build_texts_menu_keyboard(),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("pick_text:"))
    async def handle_pick_text(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        settings_key = callback.data.split(":", 1)[1]
        label, template_vars = next(
            ((label, v) for k, label, v in _TEXT_TEMPLATE_KEYS if k == settings_key),
            (settings_key, "—"),
        )
        current = db.get_setting(settings_key, "")
        await state.set_state(AdminStates.waiting_text_template)
        await state.update_data(text_settings_key=settings_key)
        preview = f"\n\n<b>Текущий кастомный текст:</b>\n{escape(current)}" if current else "\n\n<i>Используется текст по умолчанию.</i>"
        await callback.message.edit_text(
            f"<b>{label}</b>\n\n"
            "Пришлите новый текст сообщением (поддерживается HTML-разметка Telegram).\n"
            f"Доступные переменные: <code>{escape(template_vars)}</code>\n"
            "Премиум-эмодзи: <code>{emoji:ключ}</code> — можно в любом месте текста.\n\n"
            "Напишите «сброс», чтобы вернуть текст по умолчанию."
            f"{preview}",
            parse_mode="HTML",
            reply_markup=build_back_keyboard(db, "admin_texts"),
        )
        await callback.answer()

    @dp.message(AdminStates.waiting_text_template)
    async def handle_text_template_input(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        settings_key = data.get("text_settings_key")
        if not settings_key:
            await state.clear()
            return
        raw = (message.text or "").strip()
        if raw.lower() in ("сброс", "reset", "-"):
            db.set_setting(settings_key, "")
            await state.clear()
            await message.answer("✅ Текст сброшен к значению по умолчанию.", reply_markup=build_back_keyboard(db, "admin_texts"))
            return
        if settings_key.endswith("_button_text"):
            # Текст кнопки — plain-текст без HTML-разметки, она бы просто
            # показалась как есть, а не отрендерилась.
            db.set_setting(settings_key, raw)
        else:
            db.set_setting(settings_key, message.html_text or raw)
        await state.clear()
        await message.answer("✅ Текст обновлён.", reply_markup=build_back_keyboard(db, "admin_texts"))

    @dp.callback_query(F.data == "admin_stats")
    async def handle_admin_stats(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        total_users = db.count_users()
        active_subs = db.count_active_subs()
        total_stars = db.total_stars_earned()
        total_crypto = db.total_crypto_payments()
        total_refs = db.count_referrals_total()
        channel = db.get_setting("required_channel", "") or "не задан"
        text = (
            "<b>📊 Статистика</b>\n\n"
            f"👥 Всего пользователей: <b>{total_users}</b>\n"
            f"✅ Активных подписок: <b>{active_subs}</b>\n"
            f"🔗 Приглашено по рефералке: <b>{total_refs}</b>\n"
            f"⭐️ Заработано stars: <b>{total_stars}</b>\n"
            f"💎 Оплат криптой: <b>{total_crypto}</b>\n"
            f"💰 Цена: <b>{db.get_price_stars()}⭐️</b> / <b>{db.get_price_usdt()}$</b>\n"
            f"🎁 Пробный период: <b>{db.get_trial_days()} дн.</b>\n"
            f"📢 Обязательный канал: <b>{escape(channel)}</b>"
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=build_back_keyboard(db))
        await callback.answer()

    @dp.callback_query(F.data == "admin_channel")
    async def handle_admin_channel(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        current = db.get_setting("required_channel", "") or "не задан"
        await state.set_state(AdminStates.waiting_channel)
        await callback.message.edit_text(
            "<b>📢 Обязательная подписка на канал</b>\n\n"
            f"Сейчас: <b>{escape(current)}</b>\n\n"
            "Пришлите юзернейм канала в формате <code>@channel</code> "
            "или ссылку <code>https://t.me/channel</code> "
            "(бот должен быть админом канала, чтобы проверять подписку).\n"
            "Пришлите <code>-</code>, чтобы отключить обязательную подписку.",
            parse_mode="HTML",
            reply_markup=build_back_keyboard(db),
        )
        await callback.answer()

    def _normalize_channel_input(text: str) -> Optional[str]:
        """Приводит @username или https://t.me/username к формату @username,
        который принимает bot.get_chat_member. Возвращает None, если формат
        не распознан."""
        text = text.strip()
        if text.startswith("http://") or text.startswith("https://"):
            handle = text.rstrip("/").rsplit("/", 1)[-1]
            handle = handle.split("?", 1)[0]
            return f"@{handle}" if handle else None
        if text.startswith("t.me/"):
            handle = text[len("t.me/"):].split("?", 1)[0]
            return f"@{handle}" if handle else None
        if text.startswith("@") and len(text) > 1:
            return text
        return None

    @dp.message(AdminStates.waiting_channel)
    async def handle_channel_input(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if text == "-":
            db.set_setting("required_channel", "")
            await state.clear()
            await message.answer("✅ Обязательная подписка на канал отключена.", reply_markup=build_back_keyboard(db))
            return
        normalized = _normalize_channel_input(text)
        if not normalized:
            await message.answer(
                "Формат: <code>@channel</code>, ссылка <code>https://t.me/channel</code> "
                "или <code>-</code> чтобы отключить.",
                parse_mode="HTML",
            )
            return
        text = normalized
        db.set_setting("required_channel", text)
        await state.clear()
        await message.answer(f"✅ Обязательный канал установлен: <b>{escape(text)}</b>", parse_mode="HTML", reply_markup=build_back_keyboard(db))

    _USERS_PAGE_SIZE = 10

    def _format_user_row(row: sqlite3.Row) -> str:
        uname = f"@{row['username']}" if row["username"] else "—"
        now = int(time.time())
        if row["sub_until"] and row["sub_until"] > now:
            sub = "✅ до " + datetime.fromtimestamp(row["sub_until"]).strftime("%d.%m.%Y")
        else:
            sub = "❌ нет"
        connected = "🔌" if row["business_connection_id"] else "—"
        refs = db.count_referrals(row["user_id"])
        return (
            f"<code>{row['user_id']}</code> {escape(uname)} | старты: {row['start_count']} | "
            f"подключён: {connected} | подписка: {sub} | рефералов: {refs}"
        )

    @dp.callback_query(F.data.startswith("admin_users:"))
    async def handle_admin_users(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user.id, settings):
            await callback.answer()
            return
        page = int(callback.data.split(":", 1)[1])
        total = db.count_users()
        rows = db.get_users_page(page * _USERS_PAGE_SIZE, _USERS_PAGE_SIZE)
        lines = [_format_user_row(r) for r in rows] or ["Пользователей нет."]
        text = f"<b>👥 Пользователи</b> (стр. {page + 1}, всего {total})\n\n" + "\n".join(lines)
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin_users:{page - 1}"))
        if (page + 1) * _USERS_PAGE_SIZE < total:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin_users:{page + 1}"))
        kb_rows = [nav] if nav else []
        kb_rows.append([_btn(db, "back", "Назад", callback_data="admin_menu")])
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
        await callback.answer()


# ======================================================================
# ENTRYPOINT
# ======================================================================


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    settings = Settings.from_env()

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())

    db = Database(settings.db_path)
    db.init_schema(settings.owner_id, settings.trial_days, settings.default_price_stars, settings.default_price_crypto_usdt)

    crypto = CryptoBotClient(settings.cryptobot_token, settings.cryptobot_testnet)

    tz = timezone(timedelta(hours=settings.timezone_offset_hours))
    clock = NicknameClock(bot, db, settings.timezone_offset_hours, owner_id=settings.owner_id)

    register_user_handlers(dp, db, clock, settings, crypto)
    register_admin_handlers(dp, db, settings)

    asyncio.create_task(run_update_loop(db, clock, tz))
    asyncio.create_task(run_seconds_loop(db, clock))
    asyncio.create_task(run_crypto_poll_loop(db, crypto, bot, settings))

    logger.info("Bot started. Owner id: %s. CryptoBot: %s", settings.owner_id, "enabled" if crypto.configured else "disabled")

    # Бот не должен останавливаться навсегда из-за временного сбоя (обрыв
    # сети, таймаут Telegram и т.п.) — перезапускаем polling в цикле.
    while True:
        try:
            await dp.start_polling(bot)
        except Exception:
            logger.exception("Polling crashed — restarting in 5s")
            await asyncio.sleep(5)
        else:
            # start_polling завершился штатно (например, вызван stop_polling)
            break


if __name__ == "__main__":
    asyncio.run(main())
