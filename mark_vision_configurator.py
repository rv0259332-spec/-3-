# -*- coding: utf-8 -*-
"""Марк — отдельная программа настройки зрения.
Показывает объекты YOLO и лица, позволяет подтвердить/переименовать объект,
сохраняет настройки и кадры-примеры локально в C:\\Robot.
"""
import json
import time
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import cv2
from PIL import Image, ImageTk
from ultralytics import YOLO

ROBOT_DIR = Path(r"C:\Robot")
CONFIG_PATH = ROBOT_DIR / "vision_config.json"
EXAMPLES_DIR = ROBOT_DIR / "vision_examples"
MODEL_NAME = "yolo11n.pt"


class MarkVisionConfigurator:
    def __init__(self, root):
        self.root = root
        self.root.title("Марк — настройка зрения")
        self.root.geometry("1100x760")
        self.root.minsize(850, 620)

        ROBOT_DIR.mkdir(parents=True, exist_ok=True)
        EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
        self.config = self.load_config()
        self.model = None
        self.camera = None
        self.running = False
        self.loading_model = True
        self.photo = None
        self.latest_frame = None
        self.latest_detections = []
        self.last_inference = 0.0
        self.busy = False
        self.last_list_signature = None
        self.face_engine = None
        try:
            from mark_vision_features import MarkVisionEngine
            self.face_engine = MarkVisionEngine(base_dir=str(ROBOT_DIR), yunet_path=str(ROBOT_DIR / "models" / "face_detection_yunet_2023mar.onnx"), object_model=MODEL_NAME, auto_download_sface=True, load_objects=False)
        except Exception as exc:
            print("Узнавание лица в конфигураторе пока недоступно:", exc)

        self.build_ui()
        self.status_var.set("Загружаю модель YOLO… При первом запуске может потребоваться интернет.")
        threading.Thread(target=self.load_model, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def load_config(self):
        default = {
            "settings": {"confidence": 0.45, "camera_index": 0, "detect_objects": True, "detect_faces": True},
            "confirmed_items": []
        }
        try:
            if CONFIG_PATH.exists():
                loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    default["settings"].update(loaded.get("settings", {}))
                    default["confirmed_items"] = loaded.get("confirmed_items", [])
        except Exception as exc:
            print("Не удалось прочитать конфигурацию:", exc)
        return default

    def build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="Настройка зрения Марка", font=("Segoe UI", 17, "bold")).pack(anchor="w")
        ttk.Label(main, text="Выбери найденный объект, подтверди или исправь название. Примеры сохраняются локально.").pack(anchor="w", pady=(2, 10))

        body = ttk.Frame(main)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        self.video_label = ttk.Label(left, text="Камера ещё не запущена", anchor="center")
        self.video_label.pack(fill="both", expand=True)

        controls = ttk.Frame(left)
        controls.pack(fill="x", pady=(8, 0))
        self.start_button = ttk.Button(controls, text="Запустить камеру", command=self.toggle_camera, state="disabled")
        self.start_button.pack(side="left")
        ttk.Label(controls, text="Камера №").pack(side="left", padx=(10, 3))
        self.camera_var = tk.IntVar(value=int(self.config["settings"].get("camera_index", 0)))
        ttk.Spinbox(controls, from_=0, to=10, width=3, textvariable=self.camera_var).pack(side="left")
        ttk.Label(controls, text="Уверенность").pack(side="left", padx=(10, 3))
        self.conf_var = tk.DoubleVar(value=float(self.config["settings"].get("confidence", 0.45)))
        ttk.Scale(controls, from_=0.20, to=0.85, variable=self.conf_var, command=self.update_conf_label).pack(side="left", fill="x", expand=True)
        self.conf_label_var = tk.StringVar(value=f"{self.conf_var.get():.2f}")
        ttk.Label(controls, textvariable=self.conf_label_var, width=4).pack(side="left", padx=4)

        right = ttk.Frame(body, padding=(12, 0, 0, 0), width=315)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        ttk.Label(right, text="Обнаруженные объекты и лица", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.detection_list = tk.Listbox(right, height=12, exportselection=False)
        self.detection_list.pack(fill="x", pady=(5, 8))
        self.detection_list.bind("<<ListboxSelect>>", self.on_select_detection)

        ttk.Label(right, text="Подтверждённое название").pack(anchor="w")
        self.name_var = tk.StringVar()
        ttk.Entry(right, textvariable=self.name_var).pack(fill="x", pady=(4, 8))
        ttk.Button(right, text="✓ Да, это нужный предмет", command=self.confirm_detection).pack(fill="x")
        ttk.Button(right, text="Запомнить моё лицо", command=self.enroll_face).pack(fill="x", pady=(6, 0))
        ttk.Button(right, text="Сохранить настройки", command=self.save_settings).pack(fill="x", pady=(6, 0))

        ttk.Separator(right).pack(fill="x", pady=12)
        ttk.Label(right, text="Сохранённые примеры", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.confirmed_list = tk.Listbox(right, height=10)
        self.confirmed_list.pack(fill="both", expand=True, pady=(5, 0))
        self.refresh_confirmed()

        self.status_var = tk.StringVar(value="Подготовка…")
        ttk.Label(main, textvariable=self.status_var, wraplength=1050).pack(anchor="w", pady=(10, 0))
        ttk.Label(main, text=f"Настройки: {CONFIG_PATH}   |   Примеры: {EXAMPLES_DIR}", foreground="#666666").pack(anchor="w", pady=(4, 0))

    def update_conf_label(self, _value=None):
        self.conf_label_var.set(f"{self.conf_var.get():.2f}")

    def load_model(self):
        try:
            model = YOLO(MODEL_NAME)
            self.root.after(0, lambda: self.model_ready(model))
        except Exception as exc:
            self.root.after(0, lambda e=str(exc): self.model_error(e))

    def model_ready(self, model):
        self.model = model
        self.loading_model = False
        self.start_button.config(state="normal")
        self.status_var.set("Модель готова. Нажми «Запустить камеру».")

    def model_error(self, error):
        self.loading_model = False
        self.status_var.set("Не удалось загрузить YOLO: " + error)
        messagebox.showerror("Ошибка YOLO", "Не удалось загрузить модель yolo11n.pt.\n\n" + error)

    def toggle_camera(self):
        if self.running:
            self.stop_camera()
        else:
            self.start_camera()

    def start_camera(self):
        index = int(self.camera_var.get())
        cam = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        try:
            cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            cam.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        except Exception:
            pass
        if not cam.isOpened():
            cam.release()
            messagebox.showerror("Камера", f"Не удалось открыть камеру №{index}. Закрой другие программы, использующие камеру.")
            return
        self.camera = cam
        self.running = True
        self.start_button.config(text="Остановить камеру")
        self.status_var.set("Камера работает. Выбери обнаружение в списке и подтверди название.")
        self.update_frame()

    def stop_camera(self):
        self.running = False
        if self.camera is not None:
            self.camera.release()
            self.camera = None
        self.start_button.config(text="Запустить камеру")
        self.video_label.config(image="", text="Камера остановлена")
        self.photo = None

    def update_frame(self):
        if not self.running or self.camera is None:
            return
        ok, frame = self.camera.read()
        if not ok:
            self.status_var.set("Не удалось получить кадр с камеры.")
            self.root.after(100, self.update_frame)
            return

        self.latest_frame = frame.copy()
        display = frame.copy()
        now = time.monotonic()
        if self.model is not None and not self.busy and now - self.last_inference >= 0.35:
            self.busy = True
            self.last_inference = now
            try:
                found = []
                # Face detection via OpenCV's bundled Haar cascade; this detects faces,
                # not a person's identity.
                if self.config["settings"].get("detect_faces", True):
                    faces = []
                    if self.face_engine is not None and self.face_engine.face_detector is not None:
                        try:
                            self.face_engine.face_detector.setInputSize((frame.shape[1], frame.shape[0]))
                            _, detected = self.face_engine.face_detector.detect(frame)
                            if detected is not None:
                                faces = [(int(f[0]), int(f[1]), int(f[2]), int(f[3])) for f in detected]
                        except Exception as exc:
                            self.status_var.set("YuNet: " + str(exc)[:120])
                    for x, y, w, h in faces:
                        found.append({"kind": "face", "label": "лицо", "confidence": None, "box": [int(x), int(y), int(x+w), int(y+h)], "class_id": None})

                if self.config["settings"].get("detect_objects", True):
                    result = self.model.predict(source=frame, imgsz=640, conf=float(self.conf_var.get()), verbose=False)[0]
                    if result.boxes is not None:
                        for box in result.boxes:
                            cls_id = int(box.cls[0].item())
                            confidence = float(box.conf[0].item())
                            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                            label = self.model.names.get(cls_id, str(cls_id))
                            found.append({"kind": "object", "label": label, "confidence": confidence, "box": [x1, y1, x2, y2], "class_id": cls_id})

                self.latest_detections = found
                for det in found:
                    x1, y1, x2, y2 = det["box"]
                    color = (60, 210, 100) if det["kind"] == "face" else (40, 190, 245)
                    text = "ЛИЦО" if det["kind"] == "face" else f"{det['label']} {det['confidence']:.0%}"
                    cv2.rectangle(display, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(display, text, (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2, cv2.LINE_AA)
                self.refresh_detection_list()
            except Exception as exc:
                self.status_var.set("Ошибка распознавания: " + str(exc))
            finally:
                self.busy = False

        rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        image.thumbnail((740, 540))
        self.photo = ImageTk.PhotoImage(image)
        self.video_label.config(image=self.photo, text="")
        self.root.after(15, self.update_frame)

    def refresh_detection_list(self):
        selection = self.detection_list.curselection()
        selected_index = selection[0] if selection else None
        signature = tuple((d["kind"], d["label"], d["class_id"]) for d in self.latest_detections)
        if signature == self.last_list_signature:
            return
        self.last_list_signature = signature
        self.detection_list.delete(0, tk.END)
        for i, det in enumerate(self.latest_detections):
            if det["kind"] == "face":
                title = f"{i+1}. Лицо"
            else:
                title = f"{i+1}. {det['label']} — {det['confidence']:.0%}"
            self.detection_list.insert(tk.END, title)
        if selected_index is not None and selected_index < len(self.latest_detections):
            self.detection_list.selection_set(selected_index)

    def on_select_detection(self, _event=None):
        selection = self.detection_list.curselection()
        if not selection or selection[0] >= len(self.latest_detections):
            return
        det = self.latest_detections[selection[0]]
        self.name_var.set(det["label"])

    def confirm_detection(self):
        selection = self.detection_list.curselection()
        if not selection or selection[0] >= len(self.latest_detections):
            messagebox.showinfo("Выбор объекта", "Сначала выбери объект или лицо в списке.")
            return
        if self.latest_frame is None:
            messagebox.showwarning("Кадр", "Кадр камеры пока недоступен.")
            return
        det = self.latest_detections[selection[0]]
        name = self.name_var.get().strip() or det["label"]
        x1, y1, x2, y2 = det["box"]
        h, w = self.latest_frame.shape[:2]
        x1, x2 = max(0, min(w, x1)), max(0, min(w, x2))
        y1, y2 = max(0, min(h, y1)), max(0, min(h, y2))
        crop = self.latest_frame[y1:y2, x1:x2]
        if crop.size == 0:
            messagebox.showwarning("Объект", "Не удалось сохранить выбранный фрагмент.")
            return
        stamp = time.strftime("%Y%m%d_%H%M%S")
        safe_kind = "face" if det["kind"] == "face" else "object"
        image_path = EXAMPLES_DIR / f"{safe_kind}_{stamp}.jpg"
        if not cv2.imwrite(str(image_path), crop):
            messagebox.showerror("Сохранение", "Не удалось сохранить изображение-пример.")
            return
        record = {
            "confirmed_name": name,
            "kind": det["kind"],
            "detected_label": det["label"],
            "class_id": det["class_id"],
            "confidence": det["confidence"],
            "box_xyxy": [x1, y1, x2, y2],
            "image": str(image_path),
            "confirmed_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.config.setdefault("confirmed_items", []).append(record)
        try:
            self.save_settings(silent=True)
            self.refresh_confirmed()
            self.status_var.set(f"Сохранён пример «{name}». YOLO автоматически не переобучалась.")
            messagebox.showinfo("Пример сохранён", f"Название: {name}\nФайл: {image_path}\n\nПример сохранён локально. Чтобы модель научилась узнавать новый предмет, позднее потребуется отдельное обучение.")
        except Exception as exc:
            messagebox.showerror("Ошибка сохранения", str(exc))

    def enroll_face(self):
        if self.latest_frame is None:
            messagebox.showinfo("Регистрация лица", "Сначала запусти камеру и дождись кадра.")
            return
        if self.face_engine is None:
            messagebox.showerror("Регистрация лица", "Модуль узнавания не загрузился. Проверь файл mark_vision_features.py рядом с robot.py.")
            return
        name = self.name_var.get().strip()
        if not name:
            name = simpledialog.askstring("Регистрация лица", "Как тебя называть?") or ""
        if not name.strip():
            return
        try:
            count = self.face_engine.enroll_from_frame(self.latest_frame.copy(), name.strip())
            self.status_var.set(f"Образ лица сохранён локально. Всего образцов: {count}.")
            messagebox.showinfo("Лицо запомнено", f"Имя: {name.strip()}\nОбразец сохранён на этом компьютере. Для надёжности зарегистрируй несколько образцов при разном освещении.")
        except Exception as exc:
            messagebox.showerror("Не удалось зарегистрировать лицо", str(exc))

    def save_settings(self, silent=False):
        self.config.setdefault("settings", {})
        self.config["settings"].update({
            "confidence": round(float(self.conf_var.get()), 2),
            "camera_index": int(self.camera_var.get()),
            "detect_objects": True,
            "detect_faces": True,
            "model": MODEL_NAME
        })
        CONFIG_PATH.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
        if not silent:
            self.status_var.set(f"Настройки сохранены: {CONFIG_PATH}")

    def refresh_confirmed(self):
        self.confirmed_list.delete(0, tk.END)
        for item in self.config.get("confirmed_items", []):
            self.confirmed_list.insert(tk.END, f"{item.get('confirmed_name', '?')} ← {item.get('detected_label', '?')}")

    def close(self):
        self.stop_camera()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = MarkVisionConfigurator(root)
    root.mainloop()
