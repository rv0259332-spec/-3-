# -*- coding: utf-8 -*-
"""Дополнительные функции зрения Марка. Все данные узнавания лица хранятся локально."""
from __future__ import annotations
import json, threading, time, urllib.request
from pathlib import Path
import cv2
import numpy as np

try:
    from ultralytics import YOLO
except Exception:
    YOLO = None
try:
    import mediapipe as mp
except Exception:
    mp = None

YUNET_URL = "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
SFACE_URL = "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"

class MarkVisionEngine:
    def __init__(self, base_dir=r"C:\Robot", yunet_path=None, object_model="yolo11n.pt", auto_download_sface=False, load_objects=True):
        self.base = Path(base_dir)
        self.models_dir = self.base / "models"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.yunet_path = Path(yunet_path) if yunet_path else self.models_dir / "face_detection_yunet_2023mar.onnx"
        self.sface_path = self.models_dir / "face_recognition_sface_2021dec.onnx"
        self.known_path = self.base / "known_faces.json"
        self.config_path = self.base / "vision_config.json"
        self.object_model_name = object_model
        self.auto_download_sface = bool(auto_download_sface)
        self.load_objects = bool(load_objects)
        self.yolo = None
        self.yolo_loading = False
        self.yolo_error = ""
        self.last_yolo = 0.0
        self.object_count = 0
        self.motion_ratio = 0.0
        self.motion_label = "движение не оценено"
        self.gesture_label = "жест не определён"
        self.face_name = "лицо не зарегистрировано"
        self._prev_gray = None
        self._last_hand = 0.0
        self._hands = None
        self._lock = threading.Lock()
        self._last_objects = []
        self._load_config()
        self._load_face_models()
        self._start_yolo()
        self._start_hands()

    def _load_config(self):
        self.config = {"settings": {"confidence": 0.40, "detect_objects": True, "detect_faces": True, "detect_motion": True, "detect_gestures": True}, "confirmed_items": []}
        try:
            if self.config_path.exists():
                data=json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(data,dict):
                    self.config["settings"].update(data.get("settings",{}))
                    self.config["confirmed_items"] = data.get("confirmed_items",[])
        except Exception as e:
            print("Настройки зрения не прочитались:", e)

    def _download(self, url, dest):
        if dest.exists() and dest.stat().st_size > 100_000:
            return True
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp=dest.with_suffix(dest.suffix+".download")
            req=urllib.request.Request(url, headers={"User-Agent":"MarkRobot/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r, open(tmp,"wb") as f:
                while True:
                    block=r.read(1024*1024)
                    if not block: break
                    f.write(block)
            if tmp.stat().st_size < 100_000:
                tmp.unlink(missing_ok=True); return False
            tmp.replace(dest)
            print("Модель сохранена:", dest)
            return True
        except Exception as e:
            print("Не удалось скачать модель", dest.name, ":", e)
            try: tmp.unlink(missing_ok=True)
            except Exception: pass
            return False

    def _load_face_models(self):
        try:
            if not self.yunet_path.exists(): self._download(YUNET_URL,self.yunet_path)
            if self.yunet_path.exists():
                self.face_detector=cv2.FaceDetectorYN.create(str(self.yunet_path),"",(320,240),0.65,0.3,5000)
            else: self.face_detector=None
        except Exception as e:
            self.face_detector=None; print("YuNet для узнавания недоступен:",e)
        try:
            if not self.sface_path.exists() and self.auto_download_sface:
                self._download(SFACE_URL,self.sface_path)
            self.face_recognizer=cv2.FaceRecognizerSF.create(str(self.sface_path),"") if self.sface_path.exists() else None
        except Exception as e:
            self.face_recognizer=None; print("SFace для узнавания недоступен:",e)
        self.known_faces=[]
        try:
            data=json.loads(self.known_path.read_text(encoding="utf-8"))
            self.known_faces=data.get("faces",[]) if isinstance(data,dict) else []
        except Exception: self.known_faces=[]
        if self.face_recognizer is None:
            print("Узнавание по имени пока выключено: модель SFace недоступна. Обнаружение лиц продолжает работать.")
        elif not self.known_faces:
            print("Для узнавания по имени открой mark_vision_configurator.py и зарегистрируй своё лицо.")

    def _start_yolo(self):
        if not self.load_objects:
            self.yolo_loading = False
            return
        if YOLO is None:
            self.yolo_error="пакет ultralytics не установлен"; return
        self.yolo_loading=True
        def worker():
            try:
                self.yolo=YOLO(self.object_model_name)
                print("YOLO готова:", self.object_model_name)
            except Exception as e:
                self.yolo_error=str(e); print("YOLO не загрузилась:",e)
            finally: self.yolo_loading=False
        threading.Thread(target=worker,daemon=True).start()

    def _start_hands(self):
        try:
            # Старый интерфейс MediaPipe Solutions используется только если он доступен.
            if mp is not None and hasattr(mp,"solutions"):
                self._hands=mp.solutions.hands.Hands(static_image_mode=False,max_num_hands=2,model_complexity=0,min_detection_confidence=0.55,min_tracking_confidence=0.5)
            else:
                self._hands=None
                print("Жесты: в этой сборке MediaPipe Solutions недоступны; остальное зрение работает.")
        except Exception as e:
            self._hands=None; print("Жесты недоступны:",e)

    def _identify_faces(self, frame):
        if self.face_detector is None or self.face_recognizer is None or not self.known_faces:
            return []
        h,w=frame.shape[:2]
        try:
            self.face_detector.setInputSize((w,h))
            _, faces=self.face_detector.detect(frame)
        except Exception: return []
        results=[]
        if faces is None: return results
        for face in faces:
            try:
                aligned=self.face_recognizer.alignCrop(frame,face)
                feat=self.face_recognizer.feature(aligned).flatten().astype(np.float32)
                best_name="Незнакомый человек"; best=-1.0
                for item in self.known_faces:
                    emb=np.asarray(item.get("embedding",[]),dtype=np.float32)
                    if emb.shape != feat.shape or not emb.size: continue
                    denom=float(np.linalg.norm(feat)*np.linalg.norm(emb))
                    score=float(np.dot(feat,emb)/denom) if denom else -1.0
                    if score>best: best=score; best_name=item.get("name","Зарегистрированный человек")
                # Conservative threshold; user can adjust after testing.
                name=best_name if best >= 0.363 else "Незнакомый человек"
                x,y,bw,bh=[int(v) for v in face[:4]]
                results.append((x,y,bw,bh,name,best))
            except Exception: continue
        return results

    def enroll_from_frame(self, frame, name):
        """Create one local face embedding from the current frame; use several varied samples for better results."""
        if self.face_detector is None or self.face_recognizer is None:
            raise RuntimeError("Модели узнавания недоступны. Проверь интернет и наличие файла face_recognition_sface_2021dec.onnx в C:\\Robot\\models.")
        h,w=frame.shape[:2]; self.face_detector.setInputSize((w,h)); _,faces=self.face_detector.detect(frame)
        if faces is None or len(faces)==0: raise RuntimeError("Лицо не найдено. Смотри прямо в камеру и добавь света.")
        face=max(faces,key=lambda f:float(f[2]*f[3]))
        aligned=self.face_recognizer.alignCrop(frame,face)
        feat=self.face_recognizer.feature(aligned).flatten().astype(float).tolist()
        data={"name":name.strip(),"embedding":feat,"created_at":time.strftime("%Y-%m-%d %H:%M:%S")}
        old=[]
        try: old=json.loads(self.known_path.read_text(encoding="utf-8")).get("faces",[])
        except Exception: pass
        # Keep multiple embeddings under same name, improving pose/lighting tolerance.
        old.append(data)
        self.known_path.write_text(json.dumps({"version":1,"faces":old[-40:]},ensure_ascii=False,indent=2),encoding="utf-8")
        self.known_faces=old[-40:]
        return len(self.known_faces)

    def process(self, frame, face_rects=None):
        """Annotate small preview frame. Returns (frame, status text)."""
        now=time.monotonic(); h,w=frame.shape[:2]
        settings=self.config.get("settings",{})
        status=[]
        # Motion estimation via frame differencing; no extra hardware required.
        if settings.get("detect_motion",True):
            gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
            gray=cv2.GaussianBlur(gray,(5,5),0)
            if self._prev_gray is not None and self._prev_gray.shape==gray.shape:
                delta=cv2.absdiff(self._prev_gray,gray)
                _,mask=cv2.threshold(delta,24,255,cv2.THRESH_BINARY)
                mask=cv2.dilate(mask,None,iterations=2)
                self.motion_ratio=float(cv2.countNonZero(mask))/float(mask.size)
                self.motion_label="движение" if self.motion_ratio>0.035 else "спокойно"
            self._prev_gray=gray
            cv2.putText(frame,f"Motion: {self.motion_label}",(6,h-8),cv2.FONT_HERSHEY_SIMPLEX,0.38,(230,230,80),1,cv2.LINE_AA)
        # YOLO object detection at a limited rate to keep camera responsive.
        if settings.get("detect_objects",True) and self.yolo is not None and now-self.last_yolo>=0.55:
            self.last_yolo=now
            try:
                result=self.yolo.predict(source=frame,imgsz=320,conf=float(settings.get("confidence",0.40)),verbose=False)[0]
                found=[]
                if result.boxes is not None:
                    for box in result.boxes:
                        cls=int(box.cls[0].item()); conf=float(box.conf[0].item())
                        x1,y1,x2,y2=[int(v) for v in box.xyxy[0].tolist()]
                        label=str(self.yolo.names.get(cls,cls))
                        found.append((x1,y1,x2,y2,label,conf))
                self._last_objects=found; self.object_count=len(found)
            except Exception as e:
                self.yolo_error=str(e)[:120]
        for x1,y1,x2,y2,label,conf in self._last_objects:
            cv2.rectangle(frame,(x1,y1),(x2,y2),(40,190,245),1)
            cv2.putText(frame,f"{label} {conf:.0%}",(x1,max(12,y1-3)),cv2.FONT_HERSHEY_SIMPLEX,0.35,(40,190,245),1,cv2.LINE_AA)
        # Identify faces locally, if the SFace model and enrolled embeddings exist.
        if settings.get("recognize_faces",True):
            identified=self._identify_faces(frame)
            for x,y,bw,bh,name,score in identified:
                color=(70,220,120) if name!="Незнакомый человек" else (80,150,245)
                cv2.putText(frame,name,(x,max(12,y-18)),cv2.FONT_HERSHEY_SIMPLEX,0.4,color,1,cv2.LINE_AA)
                if name!="Незнакомый человек": self.face_name=name
        # Hand gesture support where the installed MediaPipe exposes Hands.
        if settings.get("detect_gestures",True) and self._hands is not None and now-self._last_hand>=0.18:
            self._last_hand=now
            try:
                rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
                result=self._hands.process(rgb)
                labels=[]
                if result.multi_hand_landmarks:
                    for hand in result.multi_hand_landmarks:
                        lm=hand.landmark
                        # Approximate open-palm / fist / thumbs-up using fingertip positions.
                        extended=sum(1 for tip,pip in ((8,6),(12,10),(16,14),(20,18)) if lm[tip].y < lm[pip].y)
                        thumb_up=lm[4].y < lm[3].y and extended<=1
                        if thumb_up: labels.append("палец вверх")
                        elif extended>=3: labels.append("открытая ладонь")
                        elif extended==0: labels.append("кулак")
                if labels: self.gesture_label=", ".join(labels)
                elif result.multi_hand_landmarks is None: self.gesture_label="рука не видна"
                cv2.putText(frame,"Gesture: "+self.gesture_label[:28],(6,16),cv2.FONT_HERSHEY_SIMPLEX,0.36,(255,190,80),1,cv2.LINE_AA)
            except Exception as e:
                self.gesture_label="недоступно"
        if self.yolo_loading: status.append("YOLO загружается")
        elif self.yolo_error: status.append("YOLO: ошибка загрузки/анализа")
        else: status.append(f"объектов: {self.object_count}")
        status.append(self.motion_label)
        status.append("узнавание лица: " + ("модель готова" if self.face_recognizer is not None else "модель недоступна"))
        return frame, "; ".join(status)

    def close(self):
        try:
            if self._hands is not None: self._hands.close()
        except Exception: pass
