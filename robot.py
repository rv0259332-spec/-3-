import os, re, json, time, math, random, base64, subprocess, urllib.request, urllib.parse, webbrowser, threading, ast, operator, datetime, platform, socket, shutil, queue, asyncio, tempfile, uuid, sys, html
import http.server, socketserver
import pygame, cv2, numpy as np, sounddevice as sd, speech_recognition as sr
try:
    from mark_vision_features import MarkVisionEngine
except Exception as _vision_import_error:
    MarkVisionEngine = None
    print("Дополнительные функции зрения не загрузились:", _vision_import_error)
from openai import OpenAI
from pygrabber.dshow_graph import FilterGraph

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = types = None

try:
    import edge_tts
except ImportError:
    edge_tts = None

try:
    import qrcode
except ImportError:
    qrcode = None

# ============================================================
# MARK — BIG ALL-IN-ONE ROBOT
# Один файл. Без pywebview. Никаких дополнительных файлов,
# кроме JSON-памяти, которую робот создаст сам.
# ============================================================

W, H = 800, 600
ROBOT_NAME = "Марк"
CAMERA_NAME = "Logi C270 HD WebCam"
YUNET = r"C:\Robot\models\face_detection_yunet_2023mar.onnx"
VISION_CONFIG = r"C:\Robot\vision_config.json"
MEMORY = r"C:\Robot\robot_memory.json"
NOTES_FILE = r"C:\Robot\robot_notes.json"
DEFAULT_CITY = "Улан-Удэ"

RATE = 44100
REC_SEC = 4
VOICE_BLOCK_SEC = 0.20
VOICE_MAX_SEC = 8.0
VOICE_MIN_SEC = 0.45
VOICE_END_SILENCE = 0.90
VOICE_PRE_ROLL_SEC = 0.35
VOICE_ENABLED = True
MIC_NAME = os.getenv("ROBOT_MIC_NAME", "")
MIC_SETTINGS = r"C:\Robot\robot_settings.json"
VOICE_THRESHOLD_DEFAULT = 0.12
TTS_VOICE = os.getenv("MARK_TTS_VOICE", "ru-RU-DmitryNeural")
TTS_RATE = os.getenv("MARK_TTS_RATE", "+0%")
TTS_PITCH = os.getenv("MARK_TTS_PITCH", "+0Hz")
TTS_VOLUME = 100
PHONE_PORT = 8765
PHONE_TOKEN = uuid.uuid4().hex[:10]
PHONE_URL = ""

OR_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
GE_MODEL = "gemini-3.8-flash"

OR_KEY = os.getenv("OPENROUTER_API_KEY", "")
GE_KEY = os.getenv("GEMINI_API_KEY", "")

router = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OR_KEY
) if OR_KEY else None

gemini = genai.Client(api_key=GE_KEY) if GE_KEY and genai else None
provider = "gemini" if gemini else "openrouter"

# ---------------- Persistent memory ----------------
memory = {"owner": "", "facts": [], "conversation": []}
if os.path.exists(MEMORY):
    try:
        with open(MEMORY, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            memory.update(loaded)
    except Exception as e:
        print("Память не прочиталась:", e)

notes = []
if os.path.exists(NOTES_FILE):
    try:
        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            notes = data[-50:]
    except Exception as e:
        print("Заметки не прочитались:", e)


def save_memory():
    try:
        os.makedirs(os.path.dirname(MEMORY), exist_ok=True)
        with open(MEMORY, "w", encoding="utf-8") as f:
            json.dump(memory, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Память:", e)


def save_notes():
    try:
        os.makedirs(os.path.dirname(NOTES_FILE), exist_ok=True)
        with open(NOTES_FILE, "w", encoding="utf-8") as f:
            json.dump(notes[-50:], f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Заметки:", e)


def remember(s):
    s = str(s).strip(" .,!?;:\"'«»")[:240]
    if not s:
        return
    if s.lower() not in [str(x).lower() for x in memory.get("facts", [])]:
        memory["facts"] = (memory.get("facts", []) + [s])[-50:]
        save_memory()


def add_conversation(who, text):
    memory.setdefault("conversation", []).append({
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "who": who,
        "text": str(text)[:500]
    })
    memory["conversation"] = memory["conversation"][-30:]
    save_memory()


def mem_text():
    owner = memory.get("owner") or "не задано"
    facts = memory.get("facts", [])
    s = "Имя владельца: " + owner
    if facts:
        s += "\nФакты:\n" + "\n".join("- " + str(x) for x in facts[-12:])
    return s


# ---------------- Global state ----------------
running = True
active = True
emotion = "normal"
page = "ai"
busy = False
status = "ГОТОВ"
last_user = ""
last_answer = ""
vision_text = "Скажи: «Марк, посмотри»."
vision_panel_open = False
vision_panel_mode = "look"
speech_caption = ""
speech_caption_until = 0.0
face_found = False
face_count = 0
tracking_enabled = True
face_x = 0.5
face_y = 0.5
eye_x = 0.0
eye_y = 0.0
mic_level = 0.0
voice_threshold_level = VOICE_THRESHOLD_DEFAULT
mouth = 0.0
speaking = False
blink = 0.0
next_blink = time.time() + random.uniform(2.5, 6.0)

# timers / tools
timer_end = 0.0
timer_label = ""
stopwatch_start = 0.0
stopwatch_running = False
last_weather = ""
weather_city = DEFAULT_CITY

# games
ttt = [""] * 9
ttt_result = ""
guess_secret = random.randint(1, 100)
guess_attempts = 0
reaction_started = 0.0
reaction_wait = 0.0
reaction_state = "idle"   # idle / waiting / go / done
reaction_result = ""

# rotating idle status
idle_tips = [
    "Скажи «Марк, посмотри» — и я использую зрение.",
    "Скажи «Марк, время».",
    "Скажи «Марк, таймер на 30 секунд».",
    "Скажи «Марк, открой YouTube».",
    "Скажи «Марк, запомни, что…».",
    "Скажи «Марк, какая у меня память?».",
    "Скажи «Марк, поиграем».",
    "Скажи «Марк, покажи систему».",
    "Скажи «Марк, запиши заметку…».",
]
tip_index = 0
tip_timer = time.time() + 5


# ============================================================
# CAMERA — startup selection + auto reconnect
# ============================================================

def list_cameras():
    try:
        return [(i, str(name)) for i, name in enumerate(FilterGraph().get_input_devices())]
    except Exception as e:
        print("Камеры:", e)
        return []


def find_camera(wanted):
    for i, name in list_cameras():
        print(f"Камера {i}: {name}")
        if wanted and wanted.lower() in name.lower():
            return i
    return -1


camera_devices = list_cameras()
selected_camera_id = -1
for i, name in camera_devices:
    if CAMERA_NAME.lower() in name.lower():
        selected_camera_id = i
        break
if selected_camera_id < 0 and camera_devices:
    selected_camera_id = camera_devices[0][0]

camera = None
latest_frame = None              # ОРИГИНАЛ: не зеркалить; используется для OCR/AI
latest_frame_preview = None       # ПРЕВЬЮ: зеркальное изображение для человека
latest_frame_ocr = None           # КАДР ДЛЯ ЧТЕНИЯ: оригинал, подготовленный для Vision
frame_lock = threading.Lock()
camera_stop = threading.Event()
camera_last_ok = 0.0


def close_camera():
    global camera
    camera_stop.set()
    if camera is not None:
        try:
            camera.release()
        except Exception:
            pass
        camera = None


def open_camera(device_id):
    global camera, camera_error, camera_enabled
    if device_id is None or int(device_id) < 0:
        camera_error = "Камера не выбрана."
        camera_enabled = False
        return False
    close_camera()
    camera_stop.clear()
    try:
        c = cv2.VideoCapture(int(device_id), cv2.CAP_DSHOW)
        # Ask the driver for a small frame and compressed transport first.
        # Some webcams ignore the requested size; camera_loop handles that safely.
        try:
            c.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        try:
            c.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        except Exception:
            pass
        c.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        c.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
        if not c.isOpened():
            c.release()
            camera_error = "Не удалось открыть выбранную камеру."
            camera_enabled = False
            return False
        camera = c
        camera_enabled = True
        camera_error = ""
        print("Камера открыта:", dict(camera_devices).get(int(device_id), int(device_id)))
        return True
    except Exception as e:
        camera_error = str(e)
        camera_enabled = False
        print("Камера:", e)
        return False


# Face detection: prefer YuNet when its model file is present; otherwise
# fall back to OpenCV's bundled Haar cascade. This detects faces only; it does
# not identify who a person is.
detector = None
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)
try:
    if os.path.isfile(YUNET):
        detector = cv2.FaceDetectorYN.create(
            YUNET, "", (320, 240), 0.7, 0.3, 5000
        )
        print("Зрение: используется YuNet для обнаружения лиц.")
    elif not face_cascade.empty():
        print("Зрение: модель YuNet не найдена; используется Haar Cascade.")
    else:
        print("ВНИМАНИЕ: нет доступной модели обнаружения лиц.")
except Exception as e:
    detector = None
    print("YuNet недоступен, пробую Haar Cascade:", e)


def camera_loop():
    global face_found, face_x, face_y, face_count, vision_extra_status
    global latest_frame, latest_frame_preview, latest_frame_ocr
    global camera_last_ok, camera_error
    last_reconnect = 0.0
    while running:
        if not camera_enabled or camera is None:
            time.sleep(0.15)
            continue

        try:
            ok, raw = camera.read()
        except (MemoryError, cv2.error) as e:
            ok, raw = False, None
            camera_error = "Камера не смогла выделить память для кадра."
            print("Камера:", e)

        if not ok or raw is None:
            now = time.time()
            face_found = False
            face_count = 0
            if now - last_reconnect > 2.0 and not speaking and not busy:
                last_reconnect = now
                try:
                    print("Камера не отдала кадр — переподключаю...")
                    open_camera(selected_camera_id)
                except Exception as e:
                    print("Переподключение камеры:", e)
            time.sleep(0.10)
            continue

        try:
            camera_last_ok = time.time()

            # ВАЖНО: raw — настоящий кадр с камеры, НЕ зеркальный.
            # Именно его Марк использует для чтения текста и Vision.
            # Держим OCR-версию компактной, чтобы не повторить прежнюю проблему
            # с нехваткой памяти при работе с 1280x720.
            ocr_frame = cv2.resize(raw, (640, 480), interpolation=cv2.INTER_AREA)

            # Для слежения и обычного UI используем небольшой зеркальный кадр.
            # Зеркальность применяется ТОЛЬКО к preview.
            mirrored = raw[:, ::-1]
            frame = cv2.resize(mirrored, (320, 240), interpolation=cv2.INTER_AREA)
            del mirrored, raw

            face_rects = []
            if detector is not None:
                detector.setInputSize((320, 240))
                _, faces = detector.detect(frame)
                if faces is not None:
                    for item in faces:
                        x, y, fw, fh = [int(v) for v in item[:4]]
                        face_rects.append((x, y, fw, fh))
            elif not face_cascade.empty():
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                found = face_cascade.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=5,
                    minSize=(28, 28), flags=cv2.CASCADE_SCALE_IMAGE
                )
                face_rects = [(int(x), int(y), int(fw), int(fh)) for x, y, fw, fh in found]

            face_count = len(face_rects)
            if face_rects:
                x, y, fw, fh = max(face_rects, key=lambda r: r[2] * r[3])
                face_x = max(0.0, min(1.0, (x + fw / 2) / 320.0))
                face_y = max(0.0, min(1.0, (y + fh / 2) / 240.0))
                face_found = True
                # Draw clear face boxes on the preview shown in Mark's interface.
                for fx, fy, fw, fh in face_rects:
                    cv2.rectangle(frame, (fx, fy), (fx + fw, fy + fh), (70, 220, 120), 2)
                    cv2.putText(frame, "FACE", (fx, max(16, fy - 5)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (70, 220, 120), 1, cv2.LINE_AA)
            else:
                face_found = False

            if vision_engine is not None:
                try:
                    frame, extra_info = vision_engine.process(frame, face_rects=face_rects)
                    if extra_info:
                        vision_extra_status = extra_info
                except Exception as e:
                    vision_extra_status = "Дополнительное зрение: " + str(e)[:180]

            with frame_lock:
                # latest_frame = ORIGINAL, never mirrored.
                latest_frame = ocr_frame
                # latest_frame_preview = ONLY what is shown to the user.
                latest_frame_preview = frame
                latest_frame_ocr = ocr_frame
        except (MemoryError, cv2.error) as e:
            face_found = False
            face_count = 0
            camera_error = "Кадр слишком большой для доступной памяти; использую уменьшенные версии."
            print("Обработка камеры:", e)
            time.sleep(0.15)


# Дополнительное зрение: объекты YOLO, оценка движения, жесты и узнавание лица.
vision_engine = None
vision_extra_status = "Дополнительное зрение загружается…"
if MarkVisionEngine is not None:
    try:
        vision_engine = MarkVisionEngine(
            base_dir=r"C:\Robot",
            yunet_path=YUNET,
            object_model="yolo11n.pt"
        )
        vision_extra_status = "Объекты/движение загружаются; лицо определяется YuNet."
        print("Дополнительное зрение: YOLO, движение, жесты и локальное узнавание лица включены (доступность зависит от моделей).")
    except Exception as e:
        vision_extra_status = "Дополнительное зрение недоступно: " + str(e)
        print(vision_extra_status)

# Started after startup hardware selection.

# ============================================================
# MICROPHONE — startup selection + automatic recovery
# ============================================================
voice_queue = queue.Queue(maxsize=160)
voice_lock = threading.Lock()
manual_listen = False
voice_noise = 0.012
voice_started = False
voice_error = ""
recognizer = sr.Recognizer()

def load_mic_settings():
    global voice_threshold_level
    try:
        if os.path.exists(MIC_SETTINGS):
            with open(MIC_SETTINGS, "r", encoding="utf-8") as f:
                data = json.load(f)
            value = float(data.get("voice_threshold_level", VOICE_THRESHOLD_DEFAULT))
            voice_threshold_level = max(0.02, min(0.80, value))
    except Exception as e:
        print("Настройки микрофона:", e)


def save_mic_settings():
    try:
        os.makedirs(os.path.dirname(MIC_SETTINGS), exist_ok=True)
        with open(MIC_SETTINGS, "w", encoding="utf-8") as f:
            json.dump({
                "microphone_id": int(selected_mic_id),
                "voice_threshold_level": float(voice_threshold_level)
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Сохранение настроек микрофона:", e)


load_mic_settings()
recognizer.dynamic_energy_threshold = False
recognizer.pause_threshold = 0.8
recognizer.phrase_threshold = 0.25
recognizer.non_speaking_duration = 0.45


def list_microphones():
    result = []
    try:
        for i, d in enumerate(sd.query_devices()):
            if int(d.get("max_input_channels", 0)) > 0:
                result.append((i, str(d.get("name", f"Микрофон {i}"))))
    except Exception as e:
        print("Микрофоны:", e)
    return result


mic_devices = list_microphones()
selected_mic_id = -1
for i, name in mic_devices:
    if MIC_NAME and MIC_NAME.lower() in name.lower():
        selected_mic_id = i
        break
try:
    default_in = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
except Exception:
    default_in = -1
if selected_mic_id < 0 and isinstance(default_in, int):
    if any(i == default_in for i, _ in mic_devices):
        selected_mic_id = default_in
if selected_mic_id < 0 and mic_devices:
    selected_mic_id = mic_devices[0][0]

# Если ранее уже выбирали микрофон, пытаемся восстановить его.
try:
    if os.path.exists(MIC_SETTINGS):
        with open(MIC_SETTINGS, "r", encoding="utf-8") as f:
            _mic_saved = json.load(f)
        _saved_id = int(_mic_saved.get("microphone_id", -1))
        if any(i == _saved_id for i, _ in mic_devices):
            selected_mic_id = _saved_id
except Exception:
    pass

audio = None


def open_microphone(device_id):
    global audio, voice_started, voice_error
    try:
        if audio is not None:
            audio.stop()
            audio.close()
    except Exception:
        pass
    audio = None
    voice_started = False
    clear_voice_queue()
    if device_id is None or int(device_id) < 0:
        voice_error = "Микрофон не выбран."
        return False
    try:
        blocksize = int(RATE * VOICE_BLOCK_SEC)
        audio = sd.InputStream(
            device=int(device_id),
            channels=1,
            samplerate=RATE,
            blocksize=blocksize,
            dtype="float32",
            callback=mic_cb,
            latency="low"
        )
        audio.start()
        voice_started = True
        voice_error = ""
        print("Микрофон открыт:", dict(mic_devices).get(int(device_id), int(device_id)))
        return True
    except Exception as e:
        audio = None
        voice_started = False
        voice_error = str(e)
        print("Микрофон:", e)
        return False



def mic_cb(indata, frames, time_info, status_info):
    global mic_level
    try:
        mono = np.asarray(indata[:, 0], dtype=np.float32).copy()
        rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-9)
        mic_level = min(1.0, rms * 14.0)
        try:
            voice_queue.put_nowait(mono)
        except queue.Full:
            try:
                voice_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                voice_queue.put_nowait(mono)
            except queue.Full:
                pass
    except Exception:
        mic_level = 0.0


def clear_voice_queue():
    while True:
        try:
            voice_queue.get_nowait()
        except queue.Empty:
            break


def normalize_voice(text):
    text = re.sub(r"\s+", " ", str(text)).strip()
    text = text.replace("ё", "е").replace("Ё", "Е")
    # Google Speech Recognition иногда превращает «Марк» в похожие варианты.
    text = re.sub(r"\bмарг\b", "Марк", text, flags=re.I)
    text = re.sub(r"\bмарок\b", "Марк", text, flags=re.I)
    text = re.sub(r"\bмарка\b", "Марк", text, flags=re.I)
    text = re.sub(r"\bмарку\b", "Марк", text, flags=re.I)
    text = re.sub(r"\bмарку\b", "Марку", text, flags=re.I)
    text = re.sub(r"\bmark\b", "Марк", text, flags=re.I)
    return text


def rms_of(block):
    if block is None or len(block) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(block))) + 1e-9)


def calibrate_voice_noise(samples=10):
    """Measure quiet-room noise from the same stream used for commands."""
    global voice_noise
    values = []
    clear_voice_queue()
    deadline = time.time() + 3.0
    while len(values) < samples and time.time() < deadline:
        try:
            b = voice_queue.get(timeout=0.35)
        except queue.Empty:
            continue
        values.append(rms_of(b))
    if values:
        baseline = float(np.median(values))
        voice_noise = max(0.0035, min(0.08, baseline * 1.35))
    print(f"Шум микрофона: {voice_noise:.4f}")


def phrase_from_voice_stream():
    """Wait for speech, collect it until silence, and return float32 samples."""
    global status, manual_listen, voice_noise

    if not audio or not voice_started or not VOICE_ENABLED:
        return None

    # Порог, выбранный пользователем в настройке микрофона.
    # mic_level = RMS * 14, поэтому переводим шкалу обратно в RMS.
    selected_threshold = max(0.02, min(0.80, voice_threshold_level))
    manual_threshold = max(0.0035, selected_threshold / 14.0)
    start_threshold = manual_threshold
    end_threshold = max(0.004, manual_threshold * 0.72)

    preroll = []
    speaking_now = False
    blocks = []
    speech_time = 0.0
    silence_time = 0.0
    started_at = 0.0

    while running and VOICE_ENABLED:
        try:
            block = voice_queue.get(timeout=0.35)
        except queue.Empty:
            if not speaking_now:
                continue
            block = np.zeros(int(RATE * VOICE_BLOCK_SEC), dtype=np.float32)

        level = rms_of(block)

        if busy or speaking:
            # Do not let the robot hear its own voice or stale audio.
            preroll.clear()
            blocks.clear()
            speaking_now = False
            speech_time = 0.0
            silence_time = 0.0
            continue

        preroll.append(block)
        max_preroll = max(1, int(VOICE_PRE_ROLL_SEC / VOICE_BLOCK_SEC))
        if len(preroll) > max_preroll:
            preroll.pop(0)

        if not speaking_now:
            threshold = manual_threshold if manual_listen else start_threshold
            if level >= threshold:
                speaking_now = True
                started_at = time.time()
                status = "СЛУШАЮ"
                blocks = list(preroll)
                speech_time = 0.0
                silence_time = 0.0
                # SPACE means «принять следующую фразу», даже очень тихую.
                manual_listen = False
                continue
        else:
            blocks.append(block)
            speech_time += VOICE_BLOCK_SEC
            if level <= end_threshold:
                silence_time += VOICE_BLOCK_SEC
            else:
                silence_time = 0.0

            elapsed = time.time() - started_at
            if (
                (speech_time >= VOICE_MIN_SEC and silence_time >= VOICE_END_SILENCE)
                or elapsed >= VOICE_MAX_SEC
            ):
                if len(blocks) < 2:
                    speaking_now = False
                    blocks.clear()
                    status = "ГОТОВ"
                    continue
                data = np.concatenate(blocks).astype(np.float32)
                # Remove DC and soften clipping without changing normal speech.
                data = data - float(np.mean(data))
                peak = float(np.max(np.abs(data))) if len(data) else 0.0
                if peak > 0.99:
                    data = data / peak * 0.98
                status = "РАСПОЗНАЮ"
                return data

    return None


def recognize(data, attempts=2):
    data = np.asarray(data, dtype=np.float32).reshape(-1)
    if len(data) < int(RATE * 0.25):
        return ""

    pcm = np.clip(data, -1, 1)
    pcm = (pcm * 32767).astype(np.int16)
    audio_data = sr.AudioData(pcm.tobytes(), RATE, 2)

    for attempt in range(max(1, attempts)):
        try:
            text = recognizer.recognize_google(
                audio_data,
                language="ru-RU",
                show_all=False
            )
            text = normalize_voice(text)
            return text.strip()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            print(f"Распознавание ({attempt + 1}):", e)
            if attempt + 1 < attempts:
                time.sleep(0.5)
        except Exception as e:
            print("Распознавание:", e)
            return ""
    return ""


def voice_listener_loop():
    """Always-on speech listener. The robot waits for a natural spoken phrase."""
    global status, voice_error
    if not audio:
        return

    calibrate_voice_noise()
    next_recalibration = time.time() + 30

    while running:
        if not VOICE_ENABLED:
            clear_voice_queue()
            status = "ГОЛОС ВЫКЛ"
            time.sleep(0.2)
            continue

        if time.time() >= next_recalibration and not busy and not speaking:
            # Slow recalibration helps when a fan starts or the room gets noisier.
            calibrate_voice_noise(samples=6)
            next_recalibration = time.time() + 30

        data = phrase_from_voice_stream()
        if data is None:
            continue

        if busy or speaking:
            continue

        text = recognize(data)
        if not text:
            status = "ГОТОВ"
            continue

        print("Ты:", text)
        process_voice_text(text)


# ============================================================
# TTS — natural Microsoft Edge neural voice + fallback
# ============================================================
tts_lock = threading.Lock()

def _sapi_fallback(text):
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        f"$b=[Convert]::FromBase64String('{encoded}');"
        "$t=[Text.Encoding]::UTF8.GetString($b);"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$v=$s.GetInstalledVoices()|Where-Object{$_.VoiceInfo.Culture.Name -eq 'ru-RU'}|Select-Object -First 1;"
        "if($v){$s.SelectVoice($v.VoiceInfo.Name)};"
        "$s.Rate=0;$s.Volume=100;$s.Speak($t);$s.Dispose()"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], check=False)


def _edge_tts_file(text, path):
    cmd = [
        sys.executable, "-m", "edge_tts",
        "--voice", TTS_VOICE,
        "--rate", TTS_RATE,
        "--pitch", TTS_PITCH,
        "--volume", f"{TTS_VOLUME:+d}%",
        "--text", text,
        "--write-media", path,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def speak(text):
    global speaking, speech_caption, speech_caption_until
    text = str(text).strip()
    if not text:
        return

    with tts_lock:
        speaking = True
        speech_caption = text
        speech_caption_until = time.time() + max(4.0, min(18.0, len(text) * 0.075 + 2.5))
        try:
            if edge_tts is not None:
                path = os.path.join(tempfile.gettempdir(), f"mark_tts_{uuid.uuid4().hex}.mp3")
                try:
                    result = _edge_tts_file(text, path)
                    if result.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 500:
                        if not pygame.mixer.get_init():
                            pygame.mixer.init()
                        pygame.mixer.music.load(path)
                        pygame.mixer.music.play()
                        while pygame.mixer.music.get_busy() and running:
                            time.sleep(0.05)
                        try:
                            pygame.mixer.music.unload()
                        except Exception:
                            pass
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                        return
                    print("Edge TTS:", (result.stderr or "").strip())
                except Exception as e:
                    print("Edge TTS exception:", e)
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
            _sapi_fallback(text)
        finally:
            speaking = False

# ============================================================
# CAMERA SNAPSHOT + PHONE CONTROL SERVER
# ============================================================
def save_photo():
    # Фото сохраняем НЕ зеркальным — это настоящий кадр с камеры.
    with frame_lock:
        frame = None if latest_frame is None else latest_frame.copy()
    if frame is None:
        return ""
    folder = os.path.join(os.path.dirname(MEMORY), "photos")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(
        folder,
        "mark_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".jpg"
    )
    try:
        if cv2.imwrite(path, frame):
            return path
    except Exception as e:
        print("Снимок:", e)
    return ""


def local_ip():
    candidates = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        for info in infos:
            ip = info[4][0]
            if ip.startswith("127."):
                continue
            candidates.append(ip)
    except Exception:
        pass
    # Prefer normal LAN addresses.
    for prefix in ("192.168.", "10.", "172."):
        for ip in candidates:
            if ip.startswith(prefix):
                return ip
    return candidates[0] if candidates else "127.0.0.1"


def phone_command(command):
    command = str(command).strip()
    if not command:
        return "Пустая команда."
    try:
        # Run through the same safe local/AI pipeline without creating another thread.
        result, reply = deterministic_command(command)
        if result is not True:
            result, reply = local_command(command)
        if result == "vision":
            reply = vision()
        elif result == "vision_text":
            reply = vision("Точно прочитай видимый на изображении текст. Не выдумывай.")
        elif result == "weather":
            reply = weather(weather_city)
        elif result is True:
            pass
        else:
            raw = ask_ge(command) if provider == "gemini" and gemini else ask_or(command)
            reply = clean(raw)
        return str(reply or "Готово.")
    except Exception as e:
        print("Телефонная команда:", e)
        return "Команда не выполнена: " + str(e)


class PhoneHandler(http.server.BaseHTTPRequestHandler):
    def _auth_ok(self):
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        return q.get("token", [""])[0] == PHONE_TOKEN

    def _send(self, body, code=200, content_type="text/html; charset=utf-8"):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/" and not self._auth_ok():
            self._send("Доступ запрещён. Открой QR-код на Марке.", 403)
            return
        if parsed.path == "/":
            self._send(phone_html())
            return
        if parsed.path == "/api/state":
            self._send(json.dumps({
                "status": status,
                "emotion": emotion,
                "provider": provider,
                "active": active,
                "voice": VOICE_ENABLED,
                "camera": camera_enabled and camera is not None,
                "face": face_found,
                "faces": face_count,
                "time": now_text(),
                "last_answer": last_answer,
            }, ensure_ascii=False), content_type="application/json; charset=utf-8")
            return
        if parsed.path == "/api/frame":
            with frame_lock:
                frame = latest_frame_preview if latest_frame_preview is not None else latest_frame
                if frame is None:
                    self._send(b"", 404, "image/jpeg")
                    return
                ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 62])
            self._send(buf.tobytes() if ok else b"", 200 if ok else 500, "image/jpeg")
            return
        self._send("Not found", 404)

    def do_POST(self):
        if not self._auth_ok():
            self._send("Доступ запрещён.", 403)
            return
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(min(length, 8000)).decode("utf-8", errors="replace")
        data = urllib.parse.parse_qs(body)
        if parsed.path == "/api/command":
            command = data.get("command", [""])[0]
            reply = phone_command(command)
            self._send(json.dumps({"reply": reply}, ensure_ascii=False), content_type="application/json; charset=utf-8")
            return
        self._send("Not found", 404)


def phone_html():
    token = html.escape(PHONE_TOKEN)
    return f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Марк — управление</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;background:#0b0f15;color:#eef2f7}}main{{max-width:760px;margin:auto;padding:18px}}.card{{background:#161d27;border:1px solid #303b4a;border-radius:18px;padding:16px;margin:12px 0}}button,input{{font-size:18px;border-radius:12px;border:1px solid #394657;padding:12px;background:#232d3a;color:#fff}}button{{min-height:48px;cursor:pointer}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}.row{{display:flex;gap:10px;flex-wrap:wrap}}input{{width:100%;box-sizing:border-box}}img{{width:100%;max-height:430px;object-fit:contain;background:#05070a;border-radius:14px}}small{{color:#9da8b5}}#reply{{white-space:pre-wrap;min-height:30px}}
</style></head><body><main>
<h1>Марк</h1><div class="card"><b id="state">Подключение…</b><div><small id="meta"></small></div></div>
<div class="card"><h2>Команды</h2><div class="grid">
<button data-c="Марк, улыбнись">😊 Улыбнись</button><button data-c="Марк, удивись">😮 Удивись</button>
<button data-c="Марк, грусти">😔 Грусть</button><button data-c="Марк, подмигни">😉 Подмигни</button>
<button data-c="Марк, посмотри">👁 Посмотри</button><button data-c="Марк, который час">🕒 Время</button>
<button data-c="Марк, погода">🌤 Погода</button><button data-c="Марк, сделай фото">📷 Снимок</button>
<button data-c="Марк, запомни, что я управляю тобой с телефона">🧠 Запомнить</button><button data-c="Марк, покажи систему">💻 Система</button>
</div></div>
<div class="card"><h2>Своя команда</h2><form id="f"><input id="cmd" autocomplete="off" placeholder="Например: таймер на 30 секунд"><div style="height:10px"></div><button type="submit">Отправить Марку</button></form><p id="reply"></p></div>
<div class="card"><h2>Камера</h2><img id="cam" src="/api/frame?token={token}" alt="Последний кадр камеры"><div class="row"><button id="refresh">Обновить кадр</button><button data-c="Марк, выключи камеру">Выключить камеру</button><button data-c="Марк, включи камеру">Включить камеру</button></div></div>
<small>Телефон и компьютер должны быть подключены к одной сети Wi-Fi/LAN.</small>
<script>
const token={json.dumps(PHONE_TOKEN)};
async function command(c){{const b=new URLSearchParams();b.set('command',c);const r=await fetch('/api/command?token='+encodeURIComponent(token),{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:b}});const j=await r.json();document.querySelector('#reply').textContent=j.reply;await state();refresh();}}
async function state(){{try{{const r=await fetch('/api/state?token='+encodeURIComponent(token));const j=await r.json();document.querySelector('#state').textContent='Статус: '+j.status+' · '+j.emotion;document.querySelector('#meta').textContent='ИИ: '+j.provider+' · Голос: '+(j.voice?'вкл':'выкл')+' · Камера: '+(j.camera?'вкл':'выкл')+' · Лиц: '+j.faces+' · '+j.time;}}catch(e){{document.querySelector('#state').textContent='Нет связи';}}}}
function refresh(){{document.querySelector('#cam').src='/api/frame?token='+encodeURIComponent(token)+'&t='+Date.now()}}
document.querySelectorAll('[data-c]').forEach(b=>b.addEventListener('click',()=>command(b.dataset.c)));
document.querySelector('#f').addEventListener('submit',e=>{{e.preventDefault();const x=document.querySelector('#cmd');command(x.value);x.value=''}});
document.querySelector('#refresh').addEventListener('click',refresh);state();setInterval(state,1500);setInterval(refresh,2500);
</script></main></body></html>'''


phone_server = None


def start_phone_server():
    global phone_server, PHONE_URL
    if phone_server is not None:
        return PHONE_URL
    PHONE_URL = f"http://{local_ip()}:{PHONE_PORT}/?token={PHONE_TOKEN}"
    try:
        phone_server = socketserver.ThreadingTCPServer(("0.0.0.0", PHONE_PORT), PhoneHandler)
        phone_server.daemon_threads = True
        threading.Thread(target=phone_server.serve_forever, daemon=True).start()
        print("Управление с телефона:", PHONE_URL)
        return PHONE_URL
    except Exception as e:
        phone_server = None
        print("Телефонный сервер:", e)
        PHONE_URL = ""
        return ""


def open_phone_control():
    url = start_phone_server()
    if url:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    return url


# ============================================================
# GENERAL UTILITIES
# ============================================================
def now_text():
    return datetime.datetime.now().strftime("%H:%M")


def date_text():
    return datetime.datetime.now().strftime("%d.%m.%Y")


def weekday_text():
    days = [
        "понедельник", "вторник", "среда", "четверг",
        "пятница", "суббота", "воскресенье"
    ]
    return days[datetime.datetime.now().weekday()]


def format_duration(seconds):
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h} ч {m:02d} мин {s:02d} сек"
    if m:
        return f"{m} мин {s:02d} сек"
    return f"{s} сек"


def system_info():
    try:
        total, used, free = shutil.disk_usage("C:\\")
        free_gb = free / (1024 ** 3)
    except Exception:
        free_gb = 0.0

    return (
        f"Windows: {platform.system()} {platform.release()}. "
        f"ПК: {platform.node()}. "
        f"Python: {platform.python_version()}. "
        f"Процессор: {platform.processor() or 'не определён'}. "
        f"Свободно на диске C: {free_gb:.1f} ГБ. "
        f"Сеть: {'есть' if internet_ok() else 'нет'}."
    )


def internet_ok():
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=1.5).close()
        return True
    except Exception:
        return False


def open_site(url):
    try:
        webbrowser.open(url)
        return True
    except Exception as e:
        print("Браузер:", e)
        return False


# ============================================================
# SAFE CALCULATOR
# ============================================================
BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def safe_calc(expr):
    expr = expr.replace(",", ".")
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**")
    expr = re.sub(r"[^0-9+\-*/().% ]", "", expr).strip()
    if not expr or len(expr) > 100:
        raise ValueError("Пустое или слишком сложное выражение.")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in BIN_OPS:
            a = ev(node.left)
            b = ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(b) > 12:
                raise ValueError("Слишком большая степень.")
            return BIN_OPS[type(node.op)](a, b)
        if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY_OPS:
            return UNARY_OPS[type(node.op)](ev(node.operand))
        raise ValueError("Недопустимое выражение.")

    result = ev(ast.parse(expr, mode="eval"))
    if not math.isfinite(float(result)):
        raise ValueError("Результат не является конечным числом.")
    if abs(result - round(result)) < 1e-10:
        return str(int(round(result)))
    return f"{result:.10g}"


# ============================================================
# WEATHER
# ============================================================
def get_json(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "MarkRobot/1.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def weather(city):
    geo_url = (
        "https://geocoding-api.open-meteo.com/v1/search?"
        + urllib.parse.urlencode({
            "name": city,
            "count": 1,
            "language": "ru",
            "format": "json"
        })
    )
    geo = get_json(geo_url)
    if not geo.get("results"):
        raise RuntimeError("Город не найден.")

    p = geo["results"][0]

    forecast_url = (
        "https://api.open-meteo.com/v1/forecast?"
        + urllib.parse.urlencode({
            "latitude": p["latitude"],
            "longitude": p["longitude"],
            "current": ",".join([
                "temperature_2m",
                "apparent_temperature",
                "relative_humidity_2m",
                "weather_code",
                "wind_speed_10m"
            ]),
            "timezone": "auto"
        })
    )

    current = get_json(forecast_url)["current"]

    desc = {
        0: "ясно",
        1: "почти ясно",
        2: "переменная облачность",
        3: "пасмурно",
        45: "туман",
        48: "туман",
        51: "морось",
        53: "морось",
        55: "сильная морось",
        61: "дождь",
        63: "дождь",
        65: "сильный дождь",
        71: "снег",
        73: "снег",
        75: "сильный снег",
        80: "ливень",
        81: "ливень",
        82: "сильный ливень",
        95: "гроза",
        96: "гроза",
        99: "гроза",
    }.get(int(current["weather_code"]), "неизвестно")

    result = (
        f'{p["name"]}: {current["temperature_2m"]} °C, '
        f'ощущается {current["apparent_temperature"]} °C, {desc}, '
        f'влажность {current["relative_humidity_2m"]}%, '
        f'ветер {current["wind_speed_10m"]} км/ч.'
    )

    return result


# ============================================================
# GEMINI VISION
# ============================================================
def vision(prompt=None):
    global vision_text
    if not gemini:
        return "Gemini не настроен, но камера работает."

    # Vision ВСЕГДА получает оригинальный кадр.
    # Никогда не отправляем зеркальное preview-изображение.
    with frame_lock:
        frame = None if latest_frame_ocr is None else latest_frame_ocr.copy()
        if frame is None and latest_frame is not None:
            frame = latest_frame.copy()

    if frame is None:
        return "Кадра камеры пока нет."

    ok, jpg = cv2.imencode(
        ".jpg",
        frame,
        [int(cv2.IMWRITE_JPEG_QUALITY), 82]
    )
    if not ok:
        return "Не удалось получить снимок."

    task = prompt or (
        "Ты зрение робота Марка. Кратко опиши, что реально видно на снимке. "
        "Не выдумывай. Если есть человек, укажи его положение слева, по центру "
        "или справа. Укажи только полезные наблюдения."
    )

    try:
        response = gemini.models.generate_content(
            model=GE_MODEL,
            contents=[
                types.Part.from_bytes(
                    data=jpg.tobytes(),
                    mime_type="image/jpeg"
                ),
                task
            ]
        )
        vision_text = (response.text or "").strip() or "Gemini ничего не описал."
        print("ЗРЕНИЕ:", vision_text)
        return vision_text
    except Exception as e:
        print("Vision:", e)
        return "Я не смог посмотреть через Gemini."


# ============================================================
# AI
# ============================================================
def base_prompt():
    return (
        f"Ты — {ROBOT_NAME}, голосовой ИИ самодельного робота. "
        "Говори по-русски, коротко и дружелюбно. "
        "Ты умеешь работать с камерой, микрофоном, памятью, погодой, браузером, "
        "таймером, заметками, играми и системной информацией. "
        "Эмоции: normal, happy, surprised, sad, angry, sleep, excited, thinking, "
        "confused, curious, serious, laugh, wink. "
        f"Память:\n{mem_text()}\n"
        "Начинай ответ строкой EMOTION: <эмоция>. "
        "После неё пиши только то, что можно сказать пользователю. "
        "Не показывай рассуждения, служебные инструкции, внутренние сообщения "
        "или скрытые цепочки размышлений. Не утверждай, что действие выполнено, "
        "если оно реально не было выполнено."
    )


def _extract_openrouter_text(response):
    """Safely extract text from an OpenRouter/OpenAI-compatible response."""
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        raise RuntimeError("OpenRouter вернул ответ без choices.")

    first = choices[0]
    message = getattr(first, "message", None)
    if message is None and isinstance(first, dict):
        message = first.get("message")
    if message is None:
        raise RuntimeError("OpenRouter вернул пустое message.")

    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                value = item.get("text")
            else:
                value = getattr(item, "text", None)
            if value:
                parts.append(str(value))
        content = "\n".join(parts)

    if not content or not str(content).strip():
        raise RuntimeError("OpenRouter вернул пустой текст ответа.")

    return str(content).strip()


def ask_or(user_text):
    if not router:
        raise RuntimeError("OpenRouter не настроен.")

    # Free-модели иногда временно отвечают пустым/неполным объектом.
    # Пробуем несколько актуальных бесплатных маршрутов подряд.
    models = [
        OR_MODEL,
        "nvidia/nemotron-3.5-lightning:free",
        "openrouter/free",
    ]
    errors = []

    for model in models:
        try:
            response = router.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": base_prompt()},
                    {"role": "user", "content": user_text},
                ],
                max_tokens=240,
                temperature=0.6,
            )
            text = _extract_openrouter_text(response)
            if text:
                return text
        except Exception as e:
            errors.append(f"{model}: {e}")
            continue

    raise RuntimeError("; ".join(errors[-3:]))


def ask_ge(user_text):
    if not gemini:
        raise RuntimeError("Gemini не настроен.")

    response = gemini.models.generate_content(
        model=GE_MODEL,
        contents=base_prompt() + "\nПользователь:\n" + user_text
    )
    return (response.text or "").strip()


def clean(raw):
    global emotion

    raw = str(raw or "").strip()

    # Некоторые free-модели иногда возвращают внутренний анализ вместо ответа.
    # Такой текст нельзя показывать пользователю и тем более отправлять в TTS.
    analysis_markers = (
        "Analyze User Input:",
        "Identify Persona & Constraints:",
        "analysis:",
        "chain of thought",
        "thinking process",
        "Let's analyze",
        "I need to",
        "Looking at the available",
        "The user message"
    )
    if any(marker.lower() in raw.lower() for marker in analysis_markers):
        print("ИИ: модель вернула внутренний анализ — ответ отброшен")
        return "Я понял тебя. Ты действительно мой создатель в рамках этого проекта, и именно ты придумал и собираешь Марка."

    match = re.search(
        r"(?im)^\s*EMOTION:\s*"
        r"(normal|happy|surprised|sad|angry|sleep|excited|thinking|confused|curious|serious|laugh|wink)"
        r"\s*$",
        raw
    )
    if match:
        emotion = match.group(1)

    result = re.sub(
        r"(?im)^\s*EMOTION:\s*"
        r"(normal|happy|surprised|sad|angry|sleep|excited|thinking|confused|curious|serious|laugh|wink)"
        r"\s*$",
        "",
        raw
    )

    result = re.sub(r"(?im)^\s*(User Safety|Response Safety)\s*:.*$", "", result)
    result = re.sub(
        r"(?is)^.*?(?:thinking process|chain of thought).*?(?:\n\n|$)",
        "",
        result
    )
    result = result.strip()

    return result or "Я получил пустой ответ."


# ============================================================
# VOICE / COMMANDS
# ============================================================
def has_name(text):
    return bool(re.search(r"\b(?:марк|марку|марко|марг|марок|mark)\b", text, re.I))


def strip_name(text):
    return re.sub(
        r"\b(?:марк|марку|mark)\b[\s,:;.!?—-]*",
        "",
        text,
        flags=re.I
    ).strip()


def set_emotion(e):
    global emotion
    allowed = {
        "normal", "happy", "surprised", "sad", "angry", "sleep",
        "excited", "thinking", "confused", "curious", "serious",
        "laugh", "wink"
    }
    if e in allowed:
        emotion = e
        return True
    return False


def emotion_cmd(text):
    t = text.lower().replace("ё", "е")
    groups = {
        "happy": ("счастлив", "улыбни", "весел", "радост"),
        "sad": ("груст", "печаль"),
        "surprised": ("удив", "в шоке"),
        "angry": ("зл", "сердит", "разозл"),
        "sleep": ("сон", "спать", "сонн"),
        "normal": ("обычн", "нормальн"),
        "excited": ("возбужд", "восторг", "волн", "энтузиазм"),
        "thinking": ("дум", "задум"),
        "confused": ("запут", "растер"),
        "curious": ("любопыт", "интерес"),
        "serious": ("серьезн", "серьез"),
        "laugh": ("смейся", "посмейся", "рассмеш"),
        "wink": ("подмиг", "моргни"),
    }
    for e, words in groups.items():
        if any(w in t for w in words):
            return e
    return None


def reset_ttt():
    global ttt, ttt_result
    ttt = [""] * 9
    ttt_result = ""


def reset_guess():
    global guess_secret, guess_attempts
    guess_secret = random.randint(1, 100)
    guess_attempts = 0


def start_reaction_game():
    global reaction_started, reaction_wait, reaction_state, reaction_result
    reaction_state = "waiting"
    reaction_result = ""
    reaction_wait = random.uniform(1.4, 4.0)
    reaction_started = time.time()


def reaction_click():
    global reaction_state, reaction_result
    if reaction_state == "waiting":
        reaction_result = "Слишком рано!"
        reaction_state = "done"
        return
    if reaction_state == "go":
        reaction_result = f"Твоя реакция: {time.time() - reaction_started:.3f} сек."
        reaction_state = "done"


def ttt_win():
    for a, b, c in (
        (0, 1, 2), (3, 4, 5), (6, 7, 8),
        (0, 3, 6), (1, 4, 7), (2, 5, 8),
        (0, 4, 8), (2, 4, 6)
    ):
        if ttt[a] and ttt[a] == ttt[b] == ttt[c]:
            return ttt[a]
    if all(ttt):
        return "DRAW"
    return None


def ttt_move(i):
    global ttt_result
    if i < 0 or i > 8 or ttt[i] or ttt_result:
        return

    ttt[i] = "X"
    result = ttt_win()
    if result:
        ttt_result = "Ты победил!" if result == "X" else "Ничья."
        return

    empty = [j for j, x in enumerate(ttt) if not x]
    if empty:
        # Иногда умный ход, иногда случайный.
        corners = [j for j in (0, 2, 6, 8) if j in empty]
        center = 4 if 4 in empty else None
        choice_pool = corners + ([center] if center is not None else [])
        if choice_pool:
            cpu_move = random.choice(choice_pool[:3] if len(choice_pool) > 3 else choice_pool)
        else:
            cpu_move = random.choice(empty)
        ttt[cpu_move] = "O"

    result = ttt_win()
    if result:
        ttt_result = "Робот победил." if result == "O" else "Ничья."


def add_note(text):
    text = str(text).strip(" .,!?:;")
    if not text:
        return False
    notes.append({
        "time": datetime.datetime.now().strftime("%d.%m %H:%M"),
        "text": text[:400]
    })
    save_notes()
    return True


def notes_text():
    if not notes:
        return "Заметок пока нет."
    return "\n".join(
        f'{i + 1}. {item["text"]} ({item["time"]})'
        for i, item in enumerate(notes[-12:])
    )


def timer_start(seconds, label="таймер"):
    global timer_end, timer_label
    timer_end = time.time() + max(1, min(int(seconds), 24 * 3600))
    timer_label = label


def parse_duration(text):
    t = text.lower().replace(",", ".").replace("ё", "е")
    total = 0

    h = re.search(r"(\d+(?:\.\d+)?)\s*(?:час|часа|часов|ч)\b", t)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:минут|минута|мин|м)\b", t)
    s = re.search(r"(\d+(?:\.\d+)?)\s*(?:секунд|секунда|сек|с)\b", t)

    if h:
        total += float(h.group(1)) * 3600
    if m:
        total += float(m.group(1)) * 60
    if s:
        total += float(s.group(1))

    if total <= 0:
        bare = re.search(r"\b(\d+(?:\.\d+)?)\b", t)
        if bare:
            total = float(bare.group(1))

    return int(total) if total > 0 else 0


def stopwatch_text():
    if stopwatch_running:
        seconds = time.time() - stopwatch_start
    elif stopwatch_start:
        seconds = stopwatch_start
    else:
        seconds = 0
    return format_duration(seconds)


def deterministic_command(text):
    """Commands that must never be delegated to the AI."""
    global page, active, emotion, weather_city
    global stopwatch_running, stopwatch_start

    low = normalize_voice(text).lower().replace("ё", "е").strip(" .,!?:;—-")

    # Time / date: answer from the PC clock immediately.
    if re.search(r"\b(?:который|сколько)\s+час", low) or any(x in low for x in (
        "время сейчас", "сколько времени", "сейчас время", "текущее время",
        "назови время", "скажи время", "время"
    )):
        page = "tools"
        return True, f"Сейчас {now_text()}."

    if any(x in low for x in (
        "какая дата", "какое сегодня число", "сегодняшняя дата",
        "какой сегодня день", "день недели", "какой день недели"
    )):
        page = "tools"
        return True, f"Сегодня {date_text()}, {weekday_text()}."

    # Basic activation/sleep.
    if any(x in low for x in ("проснись", "включись", "активируйся", "активация")):
        active = True
        emotion = "happy"
        return True, "Я активирован."

    if any(x in low for x in ("усни", "засыпай", "деактивация", "робот спать")):
        active = False
        emotion = "sleep"
        return True, "Переходу в режим ожидания."

    return False, None


def local_command(text):
    global active, emotion, page, provider
    global weather_city, last_weather
    global stopwatch_start, stopwatch_running
    global guess_attempts, guess_secret

    low = text.lower().replace("ё", "е").strip()

    # --- wake / sleep ---
    if any(x in low for x in ("активация", "проснись", "включись", "пробуждение")):
        active = True
        emotion = "happy"
        return True, "Я активирован."

    if any(x in low for x in ("деактивация", "усни", "робот спать", "режим сна")):
        active = False
        emotion = "sleep"
        return True, "Переходу в режим ожидания."

    # --- identity ---
    if any(x in low for x in ("кто ты", "как тебя зовут", "что ты за робот")):
        return True, (
            f"Я {ROBOT_NAME}. Я твой самодельный голосовой робот с камерой, "
            "микрофоном, эмоциями, памятью, играми, погодой и ИИ."
        )

    if any(x in low for x in ("что ты умеешь", "твои функции", "возможности")):
        page = "help"
        return True, (
            "Я умею слушать, говорить, следить за лицом, смотреть через Gemini, "
            "запоминать факты, хранить заметки, считать, показывать время и дату, "
            "ставить таймер и секундомер, узнавать погоду, открывать сайты, "
            "играть в крестики-нолики и реакционную игру, показывать систему "
            "и переключать ИИ-провайдера."
        )

    # --- owner / memory ---
    m = re.match(r"меня зовут\s+(.+)$", text, re.I)
    if m:
        name = m.group(1).strip(" .,!?;:\"'«»")
        memory["owner"] = name[:80]
        save_memory()
        return True, f"Запомнил. Тебя зовут {name}."

    m = re.match(r"запомни(?:,)?\s*(.+)$", text, re.I)
    if m:
        remember(m.group(1))
        return True, "Хорошо, я это запомнил."

    if any(x in low for x in (
        "что ты помнишь",
        "покажи память",
        "что ты обо мне помнишь",
        "расскажи память"
    )):
        page = "memory"
        return True, mem_text()

    if any(x in low for x in ("очисти память", "забудь все", "забудь всё")):
        memory["owner"] = ""
        memory["facts"] = []
        memory["conversation"] = []
        save_memory()
        return True, "Память очищена."

    # --- notes ---
    m = re.match(r"(?:запиши заметку|заметка|сделай заметку)\s*(.*)$", text, re.I)
    if m:
        note = m.group(1).strip(" .,:")
        if note:
            add_note(note)
            page = "notes"
            return True, "Заметка сохранена."
        page = "notes"
        return True, notes_text()

    if any(x in low for x in ("покажи заметки", "мои заметки", "что в заметках")):
        page = "notes"
        return True, notes_text()

    if "очисти заметки" in low:
        notes.clear()
        save_notes()
        return True, "Заметки очищены."

    # --- emotions ---
    e = emotion_cmd(text)
    if e:
        set_emotion(e)
        names = {
            "normal": "Возвращаю обычное лицо.",
            "happy": "Готово. Я улыбаюсь.",
            "sad": "Готово. Я грущу.",
            "surprised": "Ого! Я удивился.",
            "angry": "Готово. Включаю серьёзно-сердитое выражение.",
            "sleep": "Я сонный.",
            "excited": "Я очень воодушевлён!",
            "thinking": "Думаю.",
            "confused": "Хм... Я немного запутался.",
            "curious": "Мне любопытно.",
            "serious": "Включаю серьёзное лицо.",
            "laugh": "Ха-ха!",
            "wink": "Подмигиваю."
        }
        return True, names.get(e, "Готово.")

    # --- creator / identity ---
    if (
        any(x in low for x in (
            "я твой создатель",
            "я тебя создал",
            "я твой автор",
            "ты знаешь что я твой создатель",
            "ты знаешь что я тебя создал",
            "я твой создатель"
        ))
        or re.search(r"\bты\s+знаешь\s+(?:то\s+)?что\s+я\s+(?:твой\s+)?создател", low)
    ):
        owner = str(memory.get("owner") or "").strip()
        if owner:
            return True, f"Да. Я помню, что мой создатель — {owner}."
        return True, "Да. Я запомню это: ты мой создатель."

    if any(x in low for x in (
        "кто твой создатель",
        "кто тебя создал",
        "кто твой автор"
    )):
        owner = str(memory.get("owner") or "").strip()
        return True, (
            f"Мой создатель — {owner}." if owner
            else "Имя создателя пока не сохранено в моей памяти."
        )

    # --- provider ---
    if "переключись на gemini" in low:
        if gemini:
            provider = "gemini"
            return True, "Теперь я использую Gemini."
        return True, "Gemini пока не настроен."

    if "переключись на openrouter" in low:
        if router:
            provider = "openrouter"
            return True, "Теперь я использую OpenRouter."
        return True, "OpenRouter не настроен."

    if "какой у тебя ии" in low or "какой сейчас ии" in low:
        return True, f"Сейчас я использую {provider}."

    if "калибров" in low and "микрофон" in low:
        calibrate_voice_noise(samples=12)
        return True, "Готово. Я заново измерил уровень фонового шума микрофона."

    if "выключи голос" in low or "выключи прослушивание" in low:
        toggle_voice()
        return True, "Голосовой режим выключен. Для включения нажми V или SPACE."

    if "включи голос" in low or "включи прослушивание" in low:
        toggle_voice() if not VOICE_ENABLED else None
        return True, "Голосовой режим включён."

    # --- vision ---
    if any(x in low for x in (
        "посмотри",
        "что ты видишь",
        "посмотри на меня",
        "что перед тобой",
        "что находится перед тобой",
        "хочу посмотреть",
        "покажи, что видишь",
        "посмотри вокруг"
    )):
        return "vision", None

    if any(x in low for x in (
        "прочитай, что написано",
        "прочитай текст",
        "что написано на экране",
        "что здесь написано",
        "хочу прочитать текст",
        "прочитай с камеры"
    )):
        return "vision_text", None

    # --- weather ---
    if "погод" in low or "температур" in low:
        city = weather_city
        m = re.search(r"(?:в|во)\s+(.+)$", text, re.I)
        if m:
            city = m.group(1).strip(" .,!?;:")
        weather_city = city
        page = "weather"
        return "weather", None

    # --- time / date ---
    if any(x in low for x in ("который час", "сколько времени", "время сейчас", "сколько времени сейчас")):
        return True, f"Сейчас {now_text()}."

    if any(x in low for x in ("какое сегодня число", "какая дата", "сегодняшняя дата")):
        return True, f"Сегодня {date_text()}, {weekday_text()}."

    if "календарь" in low or "день недели" in low:
        return True, f"Сегодня {weekday_text()}, {date_text()}."

    # --- calculator ---
    m = re.match(r"(?:посчитай|вычисли|сколько будет)\s+(.+)$", text, re.I)
    if m:
        try:
            return True, "Ответ: " + safe_calc(m.group(1))
        except Exception:
            return True, "Я не смог безопасно посчитать это выражение."

    # --- timer ---
    if "таймер" in low:
        if "отмени" in low or "останови" in low or "сбрось" in low:
            timer_start(0)
            return True, "Таймер остановлен."

        duration = parse_duration(low)
        if duration > 0:
            timer_start(duration, "голосовой таймер")
            page = "tools"
            return True, f"Таймер установлен на {format_duration(duration)}."

        page = "tools"
        return True, "Скажи, например: «Марк, таймер на 30 секунд»."

    # --- stopwatch ---
    if "секундомер" in low:
        if "старт" in low or "запусти" in low:
            stopwatch_running = True
            stopwatch_start = time.time()
            page = "tools"
            return True, "Секундомер запущен."
        if "останови" in low or "стоп" in low:
            if stopwatch_running:
                stopwatch_start = time.time() - stopwatch_start
                stopwatch_running = False
            page = "tools"
            return True, f"Секундомер остановлен: {stopwatch_text()}."
        if "сброс" in low:
            stopwatch_running = False
            stopwatch_start = 0.0
            page = "tools"
            return True, "Секундомер сброшен."
        page = "tools"
        return True, f"Секундомер: {stopwatch_text()}."

    # --- browser / sites ---
    if any(x in low for x in ("открой браузер", "запусти браузер")):
        page = "browser"
        open_site("https://www.google.com")
        return True, "Открываю браузер."

    if "открой youtube" in low:
        page = "browser"
        open_site("https://www.youtube.com")
        return True, "Открываю YouTube."

    if "открой google" in low or "поиск в интернете" in low:
        page = "browser"
        open_site("https://www.google.com")
        return True, "Открываю поиск."

    if "открой github" in low:
        page = "browser"
        open_site("https://github.com")
        return True, "Открываю GitHub."

    if any(x in low for x in (
        "открой музыку",
        "включи музыку",
        "запусти музыку",
        "музыку включи",
        "музыку запусти",
        "хочу музыку"
    )):
        page = "browser"
        open_site("https://music.youtube.com")
        return True, "Открываю музыку в браузере."

    # --- system ---
    if any(x in low for x in (
        "покажи систему", "информация о системе", "состояние компьютера",
        "характеристики компьютера", "системная информация"
    )):
        page = "system"
        return True, system_info()

    if "есть интернет" in low or "интернет работает" in low:
        return True, "Интернет доступен." if internet_ok() else "Интернета сейчас нет."

    # --- apps ---
    if "открой калькулятор" in low:
        try:
            subprocess.Popen(["calc.exe"])
            return True, "Запускаю калькулятор Windows."
        except Exception:
            return True, "Не получилось открыть калькулятор."

    if "открой блокнот" in low:
        try:
            subprocess.Popen(["notepad.exe"])
            return True, "Запускаю блокнот."
        except Exception:
            return True, "Не получилось открыть блокнот."

    # --- games ---
    if any(x in low for x in ("крестики-нолики", "крестики нолики", "игра крестики")):
        reset_ttt()
        page = "games"
        return True, "Открываю крестики-нолики. Ты играешь за X."

    if any(x in low for x in ("угадай число", "игра угадай число")):
        reset_guess()
        page = "guess"
        return True, "Я загадал число от 1 до 100. Называй число."

    if "реакция" in low and ("игра" in low or "поиграем" in low):
        start_reaction_game()
        page = "reaction"
        return True, "Приготовься. Жми, когда появится сигнал."

    if "поиграем" in low or "открой игры" in low:
        page = "games"
        return True, "Открываю игры."

    # --- quick arithmetic even without the words «посчитай» ---
    if re.search(r"\d+\s*(?:\+|-|\*|/|×|÷|\^)\s*\d+", low):
        try:
            # Remove a possible transliterated wake word before calculation.
            expr = re.sub(r"\bmark\b", "", low, flags=re.I).strip()
            return True, "Ответ: " + safe_calc(expr)
        except Exception:
            pass

    # --- guess number by voice ---
    if page == "guess":
        nums = re.findall(r"\b\d{1,3}\b", low)
        if nums:
            n = int(nums[-1])
            guess_attempts += 1
            if n < guess_secret:
                return True, "Больше."
            if n > guess_secret:
                return True, "Меньше."
            result = f"Угадал! Попыток: {guess_attempts}."
            reset_guess()
            return True, result

    # --- page commands ---
    if "память" in low:
        page = "memory"
        return True, mem_text()

    if "заметки" in low:
        page = "notes"
        return True, notes_text()

    if "таймеры" in low or "инструменты" in low:
        page = "tools"
        return True, "Открываю инструменты."

    return False, None


# ============================================================
# VOICE LOOP
# ============================================================
def process_voice_text(text):
    global busy, status, last_user, last_answer, page, vision_panel_open, vision_panel_mode

    if busy:
        return

    busy = True
    try:
        text = normalize_voice(text)
        if len(text.strip()) < 2:
            return

        last_user = text
        add_conversation("user", text)

        named = has_name(text)
        command = strip_name(text) if named else text

        if not active and not named:
            status = "ОЖИДАНИЕ"
            return

        if not command:
            speak("Я слушаю.")
            status = "ГОТОВ"
            return

        result, reply = deterministic_command(command)
        if result is not True:
            result, reply = local_command(command)

        if result == "vision":
            vision_panel_open = True
            vision_panel_mode = "look"
            status = "СМОТРЮ"
            reply = vision()
            last_answer = reply
            add_conversation("robot", reply)
            speak(reply)
            status = "ГОТОВ"
            return

        if result == "vision_text":
            vision_panel_open = True
            vision_panel_mode = "text"
            status = "ЧИТАЮ"
            reply = vision(
                "Посмотри на изображение и постарайся точно прочитать видимый "
                "текст. Если текста нет, скажи об этом. Не придумывай символы."
            )
            last_answer = reply
            add_conversation("robot", reply)
            speak(reply)
            status = "ГОТОВ"
            return

        if result == "weather":
            try:
                status = "ПОГОДА"
                reply = weather(weather_city)
                last_weather = reply
                last_answer = reply
                add_conversation("robot", reply)
                speak(reply)
                status = "ГОТОВ"
            except Exception as e:
                print("Погода:", e)
                reply = "Не удалось получить погоду."
                last_answer = reply
                add_conversation("robot", reply)
                speak(reply)
                status = "ГОТОВ"
            return

        if result is True:
            last_answer = reply
            add_conversation("robot", reply)
            speak(reply)
            status = "ГОТОВ"
            return

        # Final safety net: never send obvious clock/date questions to the AI.
        guard_result, guard_reply = deterministic_command(command)
        if guard_result:
            last_answer = guard_reply
            add_conversation("robot", guard_reply)
            speak(guard_reply)
            status = "ГОТОВ"
            return

        status = provider.upper()
        set_emotion("thinking")

        try:
            raw = ask_ge(command) if provider == "gemini" else ask_or(command)
            reply = clean(raw)
        except Exception as e:
            print("ИИ:", e)
            reply = "Не получилось связаться с ИИ. Проверь интернет и API-ключ."
            set_emotion("confused")

        last_answer = reply
        add_conversation("robot", reply)
        print("Эмоция:", emotion)
        print("Ответ:", reply)

        status = "ГОВОРЮ"
        speak(reply)
        status = "ГОТОВ"

    except Exception as e:
        print("РОБОТ:", repr(e))
        status = "ОШИБКА"
        last_answer = "Ошибка. Посмотри командную строку."
    finally:
        busy = False
        clear_voice_queue()


def voice_chat():
    # Backward-compatible one-shot command: SPACE asks the always-on listener
    # to accept the next phrase even without the wake word.
    start_voice()


def start_voice():
    global manual_listen, status, VOICE_ENABLED
    VOICE_ENABLED = True
    manual_listen = True
    status = "СКАЖИТЕ КОМАНДУ"


def toggle_voice():
    global VOICE_ENABLED, manual_listen, status
    VOICE_ENABLED = not VOICE_ENABLED
    manual_listen = False
    clear_voice_queue()
    status = "ГОТОВ" if VOICE_ENABLED else "ГОЛОС ВЫКЛ"
    return VOICE_ENABLED


# ============================================================
# PYGAME UI
# ============================================================
pygame.init()
fullscreen_mode = False

def set_fullscreen(enabled):
    global screen, fullscreen_mode
    fullscreen_mode = bool(enabled)
    if fullscreen_mode:
        # SCALED keeps the existing 1100x700 interface proportions,
        # while Pygame scales it to the whole monitor.
        screen = pygame.display.set_mode(
            (W, H),
            pygame.DOUBLEBUF | pygame.FULLSCREEN
        )
    else:
        screen = pygame.display.set_mode(
            (W, H),
            pygame.DOUBLEBUF | pygame.RESIZABLE
        )
    pygame.display.set_caption(f"{ROBOT_NAME} — ROBOT")

set_fullscreen(False)
clock = pygame.time.Clock()

F1 = pygame.font.SysFont("Arial", 28, True)
F2 = pygame.font.SysFont("Arial", 20)
F3 = pygame.font.SysFont("Arial", 15)
F4 = pygame.font.SysFont("Arial", 13)

MENU_H = 66

# ---------------- Startup device selector ----------------
setup_camera_index = next((i for i, (dev_id, _) in enumerate(camera_devices) if dev_id == selected_camera_id), 0)
setup_mic_index = next((i for i, (dev_id, _) in enumerate(mic_devices) if dev_id == selected_mic_id), 0)

def _device_name(items, idx):
    if 0 <= idx < len(items):
        return items[idx][1]
    return "Не выбран"


def _button(rect, label, selected=False):
    fill = (58, 70, 89) if selected else (32, 40, 52)
    pygame.draw.rect(screen, fill, rect, border_radius=12)
    z = F3.render(label, True, (235, 240, 245))
    screen.blit(z, (rect.x + 12, rect.centery - z.get_height() // 2))


def startup_setup():
    global setup_camera_index, setup_mic_index, selected_camera_id, selected_mic_id
    global voice_threshold_level, audio, voice_started, voice_error, mic_level

    # В настройке открываем тот же поток, который потом будет использовать Марк.
    # Это позволяет видеть настоящий уровень сигнала ещё до запуска голосового слушателя.
    meter_device = None

    def restart_setup_meter():
        nonlocal meter_device
        global audio, voice_started, voice_error
        device_id = mic_devices[setup_mic_index][0] if mic_devices and 0 <= setup_mic_index < len(mic_devices) else -1
        if meter_device == device_id and audio is not None and voice_started:
            return
        try:
            if audio is not None:
                audio.stop()
                audio.close()
        except Exception:
            pass
        audio = None
        voice_started = False
        meter_device = device_id
        if device_id < 0:
            return
        try:
            info = sd.query_devices(int(device_id), "input")
            device_rate = int(float(info.get("default_samplerate", RATE)))
            audio = sd.InputStream(
                device=int(device_id),
                channels=1,
                samplerate=device_rate,
                blocksize=max(256, int(device_rate * VOICE_BLOCK_SEC)),
                dtype="float32",
                callback=mic_cb,
                latency="low"
            )
            audio.start()
            voice_started = True
            voice_error = ""
        except Exception as e:
            audio = None
            voice_started = False
            voice_error = str(e)
            print("Тест микрофона:", e)

    def stop_setup_meter():
        global audio, voice_started
        try:
            if audio is not None:
                audio.stop()
                audio.close()
        except Exception:
            pass
        audio = None
        voice_started = False

    restart_setup_meter()

    while running:
        screen.fill((8, 10, 14))
        ctr("НАСТРОЙКА МАРКА", 20, F1)
        txt("Выбери камеру и микрофон. Полоска справа показывает живой уровень голоса.", 38, 58, F3)

        # camera list
        panel(pygame.Rect(25, 95, 365, 405), "КАМЕРА")
        if not camera_devices:
            txt("Камеры не найдены.", 45, 140, F2)
        else:
            y = 135
            for i, (dev_id, name) in enumerate(camera_devices[:7]):
                rect = pygame.Rect(42, y, 331, 42)
                _button(rect, f"{i+1}. {name[:36]}", i == setup_camera_index)
                y += 46
        txt("Выбрано:", 45, 452, F4)
        txt(_device_name(camera_devices, setup_camera_index)[:42], 45, 471, F4)

        # microphone list + live level meter
        panel(pygame.Rect(410, 95, 365, 405), "МИКРОФОН")
        if not mic_devices:
            txt("Микрофоны не найдены.", 430, 140, F2)
        else:
            y = 128
            for i, (dev_id, name) in enumerate(mic_devices[:5]):
                rect = pygame.Rect(427, y, 331, 36)
                _button(rect, f"{i+1}. {name[:36]}", i == setup_mic_index)
                y += 39

        txt("Выбрано:", 430, 329, F4)
        txt(_device_name(mic_devices, setup_mic_index)[:42], 430, 347, F4)
        txt("УРОВЕНЬ ГОЛОСА", 430, 370, F4)

        # Живая шкала уровня: 0..100%.
        meter_rect = pygame.Rect(430, 391, 325, 24)
        pygame.draw.rect(screen, (24, 29, 38), meter_rect, border_radius=7)
        level_px = int(max(0.0, min(1.0, mic_level)) * (meter_rect.width - 6))
        if level_px > 0:
            pygame.draw.rect(
                screen, (80, 205, 130),
                pygame.Rect(meter_rect.x + 3, meter_rect.y + 3, level_px, meter_rect.height - 6),
                border_radius=5
            )
        threshold_x = meter_rect.x + 3 + int(max(0.0, min(1.0, voice_threshold_level)) * (meter_rect.width - 6))
        pygame.draw.line(screen, (245, 190, 70), (threshold_x, meter_rect.y - 5), (threshold_x, meter_rect.bottom + 5), 2)
        txt(f"Сигнал: {int(mic_level * 100):3d}%", 430, 421, F4)
        txt(f"Порог: {int(voice_threshold_level * 100):3d}%", 555, 421, F4)

        # Выбор порога. Меняем маленькими шагами, чтобы можно было точно подобрать чувствительность.
        minus_btn = pygame.Rect(430, 445, 45, 34)
        plus_btn = pygame.Rect(710, 445, 45, 34)
        _button(minus_btn, "−")
        _button(plus_btn, "+")
        bar = pygame.Rect(486, 452, 214, 20)
        pygame.draw.rect(screen, (31, 37, 48), bar, border_radius=8)
        fill_w = int(max(0.0, min(1.0, voice_threshold_level)) * bar.width)
        if fill_w:
            pygame.draw.rect(screen, (245, 190, 70), pygame.Rect(bar.x, bar.y, fill_w, bar.height), border_radius=8)
        txt("Тише", 486, 478, F4)
        txt("Громче", 660, 478, F4)

        # Нижняя панель действий: fullscreen отдельно от ГОТОВО.
        fullscreen_btn = pygame.Rect(150, 525, 230, 48)
        done = pygame.Rect(400, 525, 230, 48)
        _button(fullscreen_btn, "ПОЛНЫЙ ЭКРАН", fullscreen_mode)
        _button(done, "ГОТОВО", True)

        txt("Жёлтая отметка — выбранная чувствительность. Говори и смотри, как растёт зелёная шкала.", 190, 585, F4)

        if voice_error:
            txt("Ошибка микрофона: " + voice_error[:58], 430, 505, F4)

        pygame.display.flip()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                stop_setup_meter()
                return False
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    stop_setup_meter()
                    return False
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    selected_camera_id = camera_devices[setup_camera_index][0] if camera_devices else -1
                    selected_mic_id = mic_devices[setup_mic_index][0] if mic_devices else -1
                    save_mic_settings()
                    stop_setup_meter()
                    return True
                if event.key == pygame.K_UP:
                    setup_camera_index = max(0, setup_camera_index - 1)
                elif event.key == pygame.K_DOWN:
                    setup_camera_index = min(max(0, len(camera_devices)-1), setup_camera_index + 1)
                elif event.key == pygame.K_LEFT:
                    setup_mic_index = max(0, setup_mic_index - 1)
                    restart_setup_meter()
                elif event.key == pygame.K_RIGHT:
                    setup_mic_index = min(max(0, len(mic_devices)-1), setup_mic_index + 1)
                    restart_setup_meter()
            elif ((event.type == pygame.MOUSEBUTTONDOWN and event.button == 1)
                  or event.type == pygame.FINGERDOWN):
                # Поддерживаем и мышь, и касание пальцем на сенсорном экране.
                if event.type == pygame.FINGERDOWN:
                    pos = (int(event.x * W), int(event.y * H))
                else:
                    pos = event.pos
                # Camera rows
                for i in range(min(7, len(camera_devices))):
                    if pygame.Rect(42, 135 + i*46, 331, 42).collidepoint(pos):
                        setup_camera_index = i
                # Mic rows
                changed_mic = False
                for i in range(min(5, len(mic_devices))):
                    if pygame.Rect(427, 128 + i*39, 331, 36).collidepoint(pos):
                        setup_mic_index = i
                        changed_mic = True
                if changed_mic:
                    restart_setup_meter()
                elif fullscreen_btn.collidepoint(pos):
                    set_fullscreen(not fullscreen_mode)
                elif minus_btn.collidepoint(pos):
                    voice_threshold_level = max(0.02, voice_threshold_level - 0.02)
                elif plus_btn.collidepoint(pos):
                    voice_threshold_level = min(0.80, voice_threshold_level + 0.02)
                elif bar.collidepoint(pos):
                    voice_threshold_level = max(0.02, min(0.80, (pos[0] - bar.x) / bar.width))
                elif done.collidepoint(pos):
                    selected_camera_id = camera_devices[setup_camera_index][0] if camera_devices else -1
                    selected_mic_id = mic_devices[setup_mic_index][0] if mic_devices else -1
                    save_mic_settings()
                    stop_setup_meter()
                    return True
        clock.tick(30)
    stop_setup_meter()
    return False

pages = [
    "ai", "weather", "camera", "browser", "games",
    "memory", "tools", "notes", "phone", "system", "help"
]
labels = [
    "AI", "ПОГОДА", "КАМЕРА", "БРАУЗЕР", "ИГРЫ",
    "ПАМЯТЬ", "ИНСТР.", "ЗАМЕТКИ", "ТЕЛЕФОН", "СИСТЕМА", "ПОМОЩЬ"
]


def menu_rects():
    count = len(labels)
    width = max(72, W // count - 4)
    return [
        pygame.Rect(
            2 + i * (W // count),
            H - MENU_H + 4,
            max(68, W // count - 5),
            MENU_H - 8
        )
        for i in range(count)
    ]


def txt(text, x, y, font=F2, color=(230, 235, 242)):
    surf = font.render(str(text), True, color)
    screen.blit(surf, (int(x), int(y)))


def ctr(text, y, font=F2, color=(230, 235, 242)):
    surf = font.render(str(text), True, color)
    screen.blit(surf, ((W - surf.get_width()) // 2, int(y)))


def wrap(text, x, y, width, font=F3, line_h=20, max_lines=12):
    words = str(text).split()
    line = ""
    lines = []

    for word in words:
        test = word if not line else line + " " + word
        if font.size(test)[0] <= width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word

    if line:
        lines.append(line)

    for i, line in enumerate(lines[:max_lines]):
        txt(line, x, y + i * line_h, font)

    return len(lines[:max_lines])


def panel(rect, title=None):
    pygame.draw.rect(screen, (17, 21, 29), rect, border_radius=15)
    pygame.draw.rect(screen, (48, 57, 70), rect, width=1, border_radius=15)
    if title:
        txt(title, rect.x + 15, rect.y + 10, F2)


def draw_face():
    left = (285, 230)
    right = (515, 230)
    size = 95

    if emotion == "sleep":
        for cx, cy in (left, right):
            pygame.draw.line(
                screen, (235, 240, 245),
                (cx - 60, cy), (cx + 60, cy), 10
            )

    elif blink > 0:
        h = max(5, int(size * (1 - blink)))
        for cx, cy in (left, right):
            pygame.draw.ellipse(
                screen,
                (235, 240, 245),
                (cx - size, cy - h // 2, size * 2, h)
            )

    else:
        for cx, cy in (left, right):
            pygame.draw.ellipse(
                screen,
                (235, 240, 245),
                (
                    cx - size,
                    cy - int(size * 0.7),
                    size * 2,
                    int(size * 1.4)
                )
            )

            px = int(cx + eye_x)
            py = int(cy + eye_y)

            if emotion in ("wink",):
                pygame.draw.line(
                    screen, (20, 20, 25),
                    (px - 30, py), (px + 30, py), 9
                )
            else:
                pupil = int(size * 0.42)
                pygame.draw.circle(screen, (20, 20, 25), (px, py), pupil)
                pygame.draw.circle(
                    screen,
                    (255, 255, 255),
                    (
                        int(px - pupil * 0.30),
                        int(py - pupil * 0.30)
                    ),
                    max(2, int(pupil * 0.22))
                )

    if emotion == "angry":
        pygame.draw.line(
            screen, (230, 230, 235),
            (205, 150), (350, 185), 11
        )
        pygame.draw.line(
            screen, (230, 230, 235),
            (450, 185), (595, 150), 11
        )

    elif emotion == "sad":
        pygame.draw.line(
            screen, (230, 230, 235),
            (205, 185), (350, 150), 10
        )
        pygame.draw.line(
            screen, (230, 230, 235),
            (450, 150), (595, 185), 10
        )

    elif emotion == "surprised":
        pygame.draw.line(
            screen, (230, 230, 235),
            (210, 110), (350, 105), 10
        )
        pygame.draw.line(
            screen, (230, 230, 235),
            (450, 105), (590, 110), 10
        )

    elif emotion in ("thinking", "curious"):
        pygame.draw.arc(
            screen, (230, 230, 235),
            pygame.Rect(205, 105, 145, 70),
            math.radians(185), math.radians(345), 9
        )

    elif emotion == "serious":
        pygame.draw.line(
            screen, (230, 230, 235),
            (205, 175), (350, 160), 9
        )
        pygame.draw.line(
            screen, (230, 230, 235),
            (450, 160), (595, 175), 9
        )

    level = mouth
    if emotion == "sleep":
        level = 0
    if emotion == "happy":
        level *= 0.75
    if emotion == "sad":
        level *= 0.50
    if emotion == "surprised":
        level = max(level, 0.55)

    mw = 145
    mh = max(6, int(8 + level * 70))
    cx, cy = W // 2, 405

    if emotion in ("happy", "laugh"):
        pygame.draw.arc(
            screen,
            (30, 30, 35),
            pygame.Rect(cx - mw // 2, cy - 10, mw, 45),
            math.radians(10),
            math.radians(170),
            8
        )

    elif emotion == "sad":
        pygame.draw.arc(
            screen,
            (30, 30, 35),
            pygame.Rect(cx - mw // 2, cy - 4, mw, 45),
            math.radians(190),
            math.radians(350),
            8
        )

    else:
        pygame.draw.ellipse(
            screen,
            (25, 25, 30),
            (cx - mw // 2, cy - mh // 2, mw, mh)
        )


def draw_mini_face(x, y, scale=1.0):
    """Compact Mark face used when the vision window is open."""
    w = int(190 * scale)
    h = int(150 * scale)
    r = pygame.Rect(int(x), int(y), w, h)
    pygame.draw.rect(screen, (12, 16, 23), r, border_radius=max(10, int(18 * scale)))
    pygame.draw.rect(screen, (54, 65, 82), r, width=2, border_radius=max(10, int(18 * scale)))
    ex = int(35 * scale)
    ey = int(52 * scale)
    er = max(10, int(24 * scale))
    for cx in (r.x + int(58 * scale), r.x + int(132 * scale)):
        pygame.draw.ellipse(screen, (235, 240, 245), (cx-er, r.y+ey-er, er*2, er*2))
        pygame.draw.circle(screen, (25, 28, 35), (cx + int(eye_x * scale), r.y+ey + int(eye_y * scale)), max(4, int(9 * scale)))
    mouth_w = int(70 * scale)
    mouth_h = max(4, int(5 + mouth * 18 * scale))
    pygame.draw.ellipse(screen, (25, 28, 35), (r.centerx-mouth_w//2, r.y+int(103*scale), mouth_w, mouth_h))
    txt("МАРК", r.x + int(12*scale), r.bottom - int(27*scale), F4)
    return r


def draw_speech_caption():
    if not speech_caption or time.time() >= speech_caption_until:
        return
    rect = pygame.Rect(18, H - MENU_H - 76, W - 36, 64)
    pygame.draw.rect(screen, (12, 16, 23), rect, border_radius=14)
    pygame.draw.rect(screen, (66, 78, 96), rect, width=1, border_radius=14)
    txt("МАРК", rect.x + 14, rect.y + 8, F4)
    wrap(speech_caption, rect.x + 72, rect.y + 8, rect.w - 88, F3, 20, 2)


def draw_vision_panel():
    """Full-screen in-app vision workspace; Mark stays visible in the corner."""
    global vision_panel_open
    overlay = pygame.Surface((W, H), pygame.SRCALPHA)
    overlay.fill((4, 7, 11, 245))
    screen.blit(overlay, (0, 0))

    title = "ЧТО Я ВИЖУ" if vision_panel_mode == "look" else "ЧТЕНИЕ ТЕКСТА"
    ctr(title, 18, F1)

    panel(pygame.Rect(24, 62, 510, 420), "КАМЕРА")
    with frame_lock:
        frame = None if latest_frame_ocr is None else latest_frame_ocr.copy()
    if frame is not None:
        try:
            frame = cv2.resize(frame, (480, 360), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            surf = pygame.surfarray.make_surface(np.transpose(rgb, (1, 0, 2)))
            screen.blit(surf, (39, 94))
        except Exception:
            txt("Не удалось показать кадр.", 45, 120, F2)
    else:
        txt("Кадр камеры пока недоступен.", 45, 120, F2)

    panel(pygame.Rect(554, 62, 222, 420), "РЕЗУЛЬТАТ")
    if vision_text:
        wrap(vision_text, 570, 100, 190, F3, 20, 16)
    else:
        txt("Ожидаю результат…", 570, 100, F3)

    # Mark remains in the lower-right corner of the vision workspace.
    draw_mini_face(575, 320, 0.88)

    close_rect = pygame.Rect(554, 500, 105, 38)
    again_rect = pygame.Rect(671, 500, 105, 38)
    _button(close_rect, "ЗАКРЫТЬ")
    _button(again_rect, "ОБНОВИТЬ", True)
    txt("ESC — закрыть", 24, 505, F4)
    txt("Скажи «Марк, прочитай текст» или «посмотри», чтобы запустить режим снова.", 24, 535, F4)


def draw_ai():
    global tip_index, tip_timer

    if time.time() >= tip_timer:
        tip_index = (tip_index + 1) % len(idle_tips)
        tip_timer = time.time() + 5

    ctr(ROBOT_NAME.upper(), 8, F1)
    draw_face()

    # info panels
    panel(pygame.Rect(14, 438, 370, 75), "СОСТОЯНИЕ")
    txt("ИИ: " + provider.upper(), 28, 466, F3)
    txt("СТАТУС: " + status, 28, 486, F3)
    txt("ГОЛОС: " + ("ВКЛ" if VOICE_ENABLED else "ВЫКЛ"), 200, 486, F3)
    txt("ЭМОЦИЯ: " + emotion.upper(), 200, 466, F3)
    txt("ЛИЦ: " + str(face_count), 200, 486, F3)
    txt("КАМЕРА: " + ("ВКЛ" if camera_enabled else "ВЫКЛ"), 290, 486, F4)

    panel(pygame.Rect(404, 438, 382, 75), "ПОДСКАЗКА")
    wrap(idle_tips[tip_index], 418, 467, 350, F3, 18, 2)

    voice_rect = pygame.Rect(390, 500, 95, 30)
    pygame.draw.rect(screen, (42, 52, 66), voice_rect, border_radius=8)
    z = F4.render("ГОЛОС ВКЛ" if VOICE_ENABLED else "ГОЛОС ВЫКЛ", True, (235, 240, 245))
    screen.blit(z, (voice_rect.centerx - z.get_width() // 2, voice_rect.centery - z.get_height() // 2))

    if last_user:
        txt("Ты: " + last_user[:58], 16, 520, F4)
    if last_answer:
        txt("Марк: " + last_answer[:58], 16, 538, F4)
    if speaking:
        txt("● ГОВОРЮ", 650, 520, F4, (150, 220, 255))


def draw_camera():
    ctr("КАМЕРА", 18, F1)
    panel(pygame.Rect(30, 60, 740, 380), "ЖИВОЙ КАДР")
    with frame_lock:
        frame = None if latest_frame_preview is None else latest_frame_preview.copy()
    if frame is not None:
        try:
            frame = cv2.resize(frame, (700, 330))
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            surf = pygame.surfarray.make_surface(np.transpose(rgb, (1, 0, 2)))
            screen.blit(surf, (50, 90))
        except Exception:
            txt("Не удалось показать кадр.", 50, 120, F2)
    else:
        txt("Кадра нет.", 50, 120, F2)
    txt("Камера: " + ("ВКЛ" if camera_enabled else "ВЫКЛ"), 45, 460, F3)
    txt("Лиц: " + str(face_count), 210, 460, F3)
    if vision_engine is not None:
        txt("Объектов: " + str(getattr(vision_engine, "object_count", 0)), 210, 480, F3)
    txt("Слежение: " + ("ВКЛ" if tracking_enabled else "ВЫКЛ"), 310, 460, F3)
    txt("Голос: «сделай фото»", 45, 487, F4)


def draw_phone():
    ctr("ТЕЛЕФОН", 18, F1)
    panel(pygame.Rect(25, 70, 750, 400), "УПРАВЛЕНИЕ С ТЕЛЕФОНА")
    txt("Открой эту страницу на телефоне:", 48, 112, F2)
    txt(PHONE_URL or "Сервер запускается…", 48, 147, F3)
    txt("Сканируй QR-код камерой телефона.", 48, 175, F3)

    if qrcode and PHONE_URL:
        try:
            qr = qrcode.QRCode(border=2, box_size=5)
            qr.add_data(PHONE_URL)
            qr.make(fit=True)
            matrix = qr.get_matrix()
            size = len(matrix)
            scale = min(5, 320 // max(1, size))
            x0 = W - size * scale - 65
            y0 = 118
            pygame.draw.rect(screen, (255,255,255), (x0-7, y0-7, size*scale+14, size*scale+14))
            for yy, row in enumerate(matrix):
                for xx, bit in enumerate(row):
                    if bit:
                        pygame.draw.rect(screen, (0,0,0), (x0+xx*scale, y0+yy*scale, scale, scale))
        except Exception as e:
            txt("QR не удалось построить: " + str(e), 48, 220, F4)
    else:
        txt("Для QR установи пакет qrcode.", 48, 220, F2)

    btn = pygame.Rect(48, 300, 300, 65)
    _button(btn, "ОТКРЫТЬ НА ПК", True)
    txt("Команда: «Марк, управление с телефона»", 48, 392, F3)
    txt("Телефон и ПК должны быть в одной локальной сети.", 48, 420, F4)


def draw_weather():
    ctr("ПОГОДА", 20, F1)
    txt("Город: " + weather_city, 35, 74, F2)

    panel(pygame.Rect(25, 110, 750, 190), "ТЕКУЩАЯ ПОГОДА")
    wrap(
        last_weather or "Скажи: «Марк, погода в Москва»",
        42, 148, 710, F2, 28, 6
    )

    panel(pygame.Rect(25, 320, 750, 95), "ПОДСКАЗКИ")
    txt("«Марк, погода в Париже»", 42, 355, F3)
    txt("«Марк, погода»", 300, 355, F3)
    txt("Кнопка ПОГОДА открывает эту страницу.", 42, 382, F3)


def draw_browser():
    ctr("БРАУЗЕР", 20, F1)
    buttons = [
        ("GOOGLE", pygame.Rect(70, 105, 300, 70), "https://www.google.com"),
        ("YOUTUBE", pygame.Rect(430, 105, 300, 70), "https://www.youtube.com"),
        ("MUSIC", pygame.Rect(70, 200, 300, 70), "https://music.youtube.com"),
        ("GITHUB", pygame.Rect(430, 200, 300, 70), "https://github.com"),
    ]

    for label, rect, _ in buttons:
        pygame.draw.rect(screen, (36, 44, 56), rect, border_radius=14)
        z = F2.render(label, True, (235, 240, 245))
        screen.blit(
            z,
            (rect.centerx - z.get_width() // 2,
             rect.centery - z.get_height() // 2)
        )

    wrap(
        "Браузер открывается как обычное приложение Windows, "
        "а не внутри робота.",
        55, 320, 690, F2, 28, 4
    )


def draw_games():
    ctr("ИГРЫ", 15, F1)

    # ttt
    panel(pygame.Rect(20, 70, 365, 385), "КРЕСТИКИ-НОЛИКИ")
    x0, y0, c = 60, 120, 94
    for i in range(9):
        rect = pygame.Rect(
            x0 + (i % 3) * c,
            y0 + (i // 3) * c,
            82,
            82
        )
        pygame.draw.rect(
            screen, (34, 41, 52),
            rect, border_radius=10
        )
        z = F1.render(ttt[i], True, (235, 240, 245))
        screen.blit(
            z,
            (
                rect.centerx - z.get_width() // 2,
                rect.centery - z.get_height() // 2
            )
        )

    txt(ttt_result or "Ты — X", 50, 410, F3)

    # other games
    panel(pygame.Rect(405, 70, 375, 385), "ДРУГИЕ ИГРЫ")
    rect1 = pygame.Rect(430, 120, 325, 75)
    rect2 = pygame.Rect(430, 215, 325, 75)
    pygame.draw.rect(screen, (36, 44, 56), rect1, border_radius=14)
    pygame.draw.rect(screen, (36, 44, 56), rect2, border_radius=14)
    ctr_local = lambda s, r: screen.blit(
        F2.render(s, True, (235, 240, 245)),
        (r.centerx - F2.size(s)[0] // 2, r.centery - F2.size(s)[1] // 2)
    )
    ctr_local("УГАДАЙ ЧИСЛО", rect1)
    ctr_local("РЕАКЦИЯ", rect2)

    txt("Голос: «Марк, угадай число»", 430, 330, F3)
    txt("Голос: «Марк, игра на реакцию»", 430, 355, F3)


def draw_memory():
    ctr("ПАМЯТЬ", 20, F1)
    panel(pygame.Rect(25, 70, 750, 240), "ЧТО Я ПОМНЮ")
    wrap(mem_text(), 45, 110, 710, F2, 26, 7)

    panel(pygame.Rect(25, 330, 750, 95), "ГОЛОСОВЫЕ КОМАНДЫ")
    txt("«Марк, меня зовут Роман»", 45, 360, F3)
    txt("«Марк, запомни, что…»", 45, 386, F3)


def draw_tools():
    ctr("ИНСТРУМЕНТЫ", 20, F1)

    panel(pygame.Rect(25, 70, 360, 170), "ТАЙМЕР")
    if timer_end > time.time():
        left = timer_end - time.time()
        txt(format_duration(left), 55, 115, F1)
        txt(timer_label or "таймер", 55, 155, F3)
    else:
        txt("Не запущен", 55, 115, F1)

    panel(pygame.Rect(415, 70, 360, 170), "СЕКУНДОМЕР")
    txt(stopwatch_text(), 445, 115, F1)
    txt("Запуск: «Марк, секундомер старт»", 445, 155, F4)

    panel(pygame.Rect(25, 265, 750, 95), "БЫСТРЫЕ КОМАНДЫ")
    txt("«Марк, посчитай 125 * 8»", 45, 295, F3)
    txt("«Марк, открой калькулятор»", 330, 295, F3)
    txt("«Марк, который час?»", 45, 322, F3)


def draw_notes():
    ctr("ЗАМЕТКИ", 20, F1)
    panel(pygame.Rect(25, 70, 750, 350), "СОХРАНЁННЫЕ ЗАМЕТКИ")
    wrap(notes_text(), 45, 110, 710, F2, 25, 11)
    panel(pygame.Rect(25, 440, 750, 65), "ГОЛОС")
    txt("«Марк, запиши заметку купить батарейки»", 45, 466, F3)


def draw_system():
    ctr("СИСТЕМА", 20, F1)
    info = system_info()

    panel(pygame.Rect(25, 70, 750, 190), "КОМПЬЮТЕР")
    wrap(info, 45, 110, 710, F2, 26, 6)

    panel(pygame.Rect(25, 285, 750, 110), "РОБОТ")
    txt("Камера: " + dict(camera_devices).get(selected_camera_id, "не выбрана"), 45, 315, F3)
    txt("OpenRouter: " + ("OK" if router else "НЕТ"), 45, 343, F3)
    txt("Gemini: " + ("OK" if gemini else "НЕТ"), 330, 343, F3)
    txt("Микрофон: " + (dict(mic_devices).get(selected_mic_id, "нет")[:24] if audio else "НЕТ"), 570, 343, F4)
    txt("Лицо: " + ("обнаружено" if face_found else "не найдено"), 45, 370, F3)


def draw_help():
    ctr("ПОМОЩЬ", 20, F1)

    panel(pygame.Rect(25, 60, 750, 400), "ФУНКЦИИ")
    lines = [
        "ГОЛОС: активировать / уснуть / кто ты / что умеешь",
        "ПАМЯТЬ: меня зовут … / запомни … / что ты помнишь",
        "ЗРЕНИЕ: посмотри / что ты видишь / прочитай текст",
        "ПОГОДА: погода / погода в Париже",
        "ВРЕМЯ: который час / какая дата / день недели",
        "ИНСТРУМЕНТЫ: таймер / секундомер / посчитай 8*9",
        "БРАУЗЕР: открой Google / YouTube / GitHub / музыку",
        "ИГРЫ: крестики-нолики / угадай число / реакция",
        "СИСТЕМА: покажи систему / есть интернет",
        "ПРИЛОЖЕНИЯ: открой калькулятор / блокнот",
        "ИИ: переключись на OpenRouter / Gemini",
        "ЭМОЦИИ: улыбайся / удивись / грусти / подмигни",
        "ГОЛОС: Марк слушает постоянно; SPACE — принять следующую фразу; V — вкл/выкл.",
        "МИКРОФОН: «калибровка микрофона» / включить / выключить",
        "КАМЕРА: включить / выключить / сделать фото / сколько лиц / слежение",
        "ТЕЛЕФОН: «Марк, управление с телефона» — локальный сайт + QR",
        "ГОЛОС: нейроголос Edge; «голос мужской/женский», «говори медленнее/быстрее»",
    ]

    y = 95
    for line in lines:
        txt(line, 45, y, F3)
        y += 28


def draw_guess():
    ctr("УГАДАЙ ЧИСЛО", 20, F1)
    panel(pygame.Rect(70, 90, 660, 300), "ИГРА")
    txt("Я загадал число от 1 до 100.", 105, 140, F1)
    txt(f"Попыток: {guess_attempts}", 105, 195, F2)
    txt("Называй число голосом.", 105, 245, F2)
    txt("Я отвечу: больше или меньше.", 105, 285, F2)


def draw_reaction():
    global reaction_state, reaction_started

    ctr("РЕАКЦИЯ", 20, F1)
    panel(pygame.Rect(70, 80, 660, 330), "ИГРА")

    if reaction_state == "idle":
        txt("Нажми нижнюю кнопку или скажи", 110, 145, F2)
        txt("«Марк, реакция».", 110, 180, F2)

    elif reaction_state == "waiting":
        txt("ЖДИ...", 280, 165, F1)
        if time.time() - reaction_started >= reaction_wait:
            reaction_state = "go"
            reaction_started = time.time()

    elif reaction_state == "go":
        txt("ЖМИ!", 320, 155, F1)
        txt("Кликай по экрану.", 270, 205, F2)

    elif reaction_state == "done":
        wrap(reaction_result, 150, 155, 500, F1, 35, 3)


def draw_page():
    screen.fill((8, 10, 14))

    if page == "ai":
        draw_ai()
    elif page == "weather":
        draw_weather()
    elif page == "camera":
        draw_camera()
    elif page == "browser":
        draw_browser()
    elif page == "games":
        draw_games()
    elif page == "memory":
        draw_memory()
    elif page == "tools":
        draw_tools()
    elif page == "notes":
        draw_notes()
    elif page == "phone":
        draw_phone()
    elif page == "system":
        draw_system()
    elif page == "help":
        draw_help()
    elif page == "guess":
        draw_guess()
    elif page == "reaction":
        draw_reaction()


def draw_menu():
    rects = menu_rects()
    pygame.draw.rect(
        screen,
        (18, 22, 30),
        (0, H - MENU_H, W, MENU_H)
    )

    for i, rect in enumerate(rects):
        selected = pages[i] == page
        fill = (60, 70, 90) if selected else (30, 36, 46)
        pygame.draw.rect(screen, fill, rect, border_radius=11)

        z = F4.render(labels[i], True, (235, 240, 245))
        screen.blit(
            z,
            (
                rect.centerx - z.get_width() // 2,
                rect.centery - z.get_height() // 2
            )
        )


def click(pos):
    global page, provider, vision_panel_open, vision_panel_mode
    if vision_panel_open:
        if pygame.Rect(554, 500, 105, 38).collidepoint(pos):
            vision_panel_open = False
            return
        if pygame.Rect(671, 500, 105, 38).collidepoint(pos):
            vision_panel_mode = "text" if vision_panel_mode == "look" else "look"
            vision_text_local = None
            prompt = ("Точно прочитай весь видимый текст на изображении. Не выдумывай символы." if vision_panel_mode == "text" else "Кратко опиши, что реально видно на изображении. Не выдумывай.")
            threading.Thread(target=lambda: vision(prompt), daemon=True).start()
            return
        return

    rects = menu_rects()

    for i, rect in enumerate(rects):
        if rect.collidepoint(pos):
            page = pages[i]
            if page == "phone":
                start_phone_server()
            return

    if page == "phone":
        if pygame.Rect(48, 300, 300, 65).collidepoint(pos):
            open_phone_control()
            return

    if page == "camera":
        snap = pygame.Rect(45, 485, 200, 35)
        if snap.collidepoint(pos):
            save_photo()
            return

    if page == "ai":
        voice_rect = pygame.Rect(390, 500, 95, 30)
        if voice_rect.collidepoint(pos):
            toggle_voice()
            return

        zones = [
            ("normal", pygame.Rect(20, 500, 85, 30)),
            ("happy", pygame.Rect(110, 500, 85, 30)),
            ("surprised", pygame.Rect(200, 500, 105, 30)),
            ("sad", pygame.Rect(310, 500, 75, 30)),
            ("angry", pygame.Rect(390, 500, 90, 30)),
            ("wink", pygame.Rect(485, 500, 80, 30)),
            ("curious", pygame.Rect(570, 500, 90, 30)),
            ("serious", pygame.Rect(665, 500, 90, 30)),
        ]
        for e, rect in zones:
            if rect.collidepoint(pos):
                set_emotion(e)
                return

    elif page == "browser":
        buttons = [
            (pygame.Rect(70, 105, 300, 70), "https://www.google.com"),
            (pygame.Rect(430, 105, 300, 70), "https://www.youtube.com"),
            (pygame.Rect(70, 200, 300, 70), "https://music.youtube.com"),
            (pygame.Rect(430, 200, 300, 70), "https://github.com"),
        ]
        for rect, url in buttons:
            if rect.collidepoint(pos):
                open_site(url)
                return

    elif page == "games":
        x0, y0, c = 60, 120, 94
        for i in range(9):
            rect = pygame.Rect(
                x0 + (i % 3) * c,
                y0 + (i // 3) * c,
                82, 82
            )
            if rect.collidepoint(pos):
                ttt_move(i)
                return

        if pygame.Rect(430, 120, 325, 75).collidepoint(pos):
            reset_guess()
            page = "guess"
            return

        if pygame.Rect(430, 215, 325, 75).collidepoint(pos):
            start_reaction_game()
            page = "reaction"
            return

    elif page == "reaction":
        if pygame.Rect(70, 80, 660, 330).collidepoint(pos):
            reaction_click()


# ============================================================
# KEYBOARD + LOOP
# ============================================================
# ---------------- Start selected hardware + local phone control ----------------
if not startup_setup():
    running = False
else:
    open_camera(selected_camera_id)
    open_microphone(selected_mic_id)
    threading.Thread(target=camera_loop, daemon=True).start()
    if audio:
        threading.Thread(target=voice_listener_loop, daemon=True).start()
    else:
        print("ВНИМАНИЕ: голосовой режим недоступен. Можно нажать SPACE после настройки микрофона.")
    open_phone_control()

print("========================================")
print(f"{ROBOT_NAME.upper()} — BIG ROBOT")
print("Камера:", dict(camera_devices).get(selected_camera_id, selected_camera_id))
print("OpenRouter:", bool(router), " Gemini:", bool(gemini))
print("SPACE — говорить")
print("ESC — выход")
print("1..9 — страницы / тест эмоций")
print("========================================")

last_timer_alarm = 0.0

try:
    while running:
        # ---- keyboard / touch ----
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if vision_panel_open:
                        vision_panel_open = False
                    else:
                        running = False
                elif event.key == pygame.K_SPACE:
                    start_voice()
                elif event.key == pygame.K_v:
                    toggle_voice()
                elif event.key == pygame.K_1:
                    page = "ai"
                elif event.key == pygame.K_2:
                    page = "weather"
                elif event.key == pygame.K_3:
                    page = "browser"
                elif event.key == pygame.K_4:
                    page = "games"
                elif event.key == pygame.K_5:
                    page = "memory"
                elif event.key == pygame.K_6:
                    page = "tools"
                elif event.key == pygame.K_7:
                    page = "notes"
                elif event.key == pygame.K_8:
                    page = "system"
                elif event.key == pygame.K_9:
                    page = "help"
                elif event.key == pygame.K_F1:
                    set_emotion("happy")
                elif event.key == pygame.K_F2:
                    set_emotion("surprised")
                elif event.key == pygame.K_F3:
                    set_emotion("sad")
                elif event.key == pygame.K_F4:
                    set_emotion("angry")
                elif event.key == pygame.K_F5:
                    set_emotion("thinking")
                elif event.key == pygame.K_F6:
                    set_emotion("curious")
                elif event.key == pygame.K_F7:
                    set_emotion("serious")
                elif event.key == pygame.K_F8:
                    set_emotion("wink")

            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                click(event.pos)

            elif event.type == pygame.FINGERDOWN:
                click((int(event.x * W), int(event.y * H)))

        # ---- reaction game timer ----
        if page == "reaction" and reaction_state == "waiting":
            if time.time() - reaction_started >= reaction_wait:
                reaction_state = "go"
                reaction_started = time.time()

        # ---- countdown timer ----
        if timer_end > 0 and time.time() >= timer_end:
            if last_timer_alarm != timer_end:
                last_timer_alarm = timer_end
                timer_end = 0
                timer_label = ""
                threading.Thread(
                    target=speak,
                    args=("Таймер закончился!",),
                    daemon=True
                ).start()

        # ---- eyes follow face ----
        target_x = max(-28, min(28, (face_x - 0.5) * 55)) if face_found and tracking_enabled else 0
        target_y = max(-18, min(18, (face_y - 0.5) * 35)) if face_found and tracking_enabled else 0

        eye_x += (target_x - eye_x) * 0.12
        eye_y += (target_y - eye_y) * 0.12

        # ---- blinking ----
        now = time.time()
        if now >= next_blink:
            blink = 1.0
            next_blink = now + random.uniform(2.5, 6.0)

        if blink > 0:
            blink = max(0.0, blink - 0.16)

        # ---- mouth animation ----
        if speaking:
            target_mouth = 0.25 + abs(math.sin(now * 8)) * 0.6
        else:
            target_mouth = mic_level

        mouth += (target_mouth - mouth) * 0.25

        # ---- draw ----
        draw_page()
        draw_menu()
        draw_speech_caption()
        if vision_panel_open:
            draw_vision_panel()
        pygame.display.flip()
        clock.tick(60)

finally:
    running = False

    try:
        camera.release()
    except Exception:
        pass

    if audio:
        try:
            audio.stop()
            audio.close()
        except Exception:
            pass

    try:
        if phone_server is not None:
            phone_server.shutdown()
            phone_server.server_close()
    except Exception:
        pass
    pygame.quit()
    print("ROBOT STOPPED")
