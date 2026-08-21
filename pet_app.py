# -*- coding: utf-8 -*-
"""桌面宠物 MoePet —— 透明无边框置顶桌宠（PySide6 单文件）
功能：左键拖动 / 点击互动（跳跃、压扁回弹、左右抖动）/ 滚轮缩放 / 右键菜单
（聊天、摸摸头、喂吃的、走路、睡觉、跟随鼠标、调整大小、置顶开关、退出）
聊天支持读取同目录 pet_config.json 中的 API 配置调用大模型 agent。
"""
import os
import sys
import json
import math
import random
import base64
import html
import io
import ctypes
import threading
import time as _t
import urllib.request
import winreg
from ctypes import wintypes

from PySide6.QtCore import (Qt, QTimer, QPoint, QRect, QEasingCurve,
                            QVariantAnimation, Signal, QObject)
from PySide6.QtGui import QPixmap, QPainter, QFont, QIcon, QCursor
from PySide6.QtWidgets import (QApplication, QWidget, QMenu, QLabel,
                               QVBoxLayout, QHBoxLayout, QLineEdit,
                               QPushButton, QSlider, QInputDialog,
                               QFileDialog, QTextBrowser)

# ---------- 资源与配置 ----------
APP_DIR = os.path.dirname(
    os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
CFG_PATH = os.path.join(APP_DIR, "pet_config.json")

DEFAULT_CFG = {
    "api_base": "https://api.deepseek.com",
    "api_key": "",
    "model": "deepseek-chat",
    "persona": "你是用户的桌面宠物，名叫小舞。说话简短俏皮（不超过25字），语气软萌，偶尔用颜文字。",
    "max_history": 10,
}

GREET_LINES = ["你好呀~ 右键菜单有很多功能哦！", "我来啦！(ﾉ>ω<)ﾉ", "今天也要元气满满~"]
JUMP_LINES = ["嘿咻！", "飞高高~", "芜湖~", "跳跳！"]
SQUASH_LINES = ["哎呀压扁啦！", "软软的~", "弹弹弹~"]
SHAKE_LINES = ["晕晕晕…", "左右摇摆~", "别晃啦~"]
DRAG_LINES = ["别拽我呀~", "要带我去哪呀？", "带我飞~"]
FOLLOW_LINES = ["等等我！", "我来啦~", "追到你咯~"]
PAT_LINES = ["嘿嘿~", "好舒服呀~", "再摸一下嘛~"]
FEED_LINES = ["嗷呜~ 好吃！", "我还要~", "谢谢款待！"]
WALK_LINES = ["散步去~", "溜溜弯~", "动起来~"]
SLEEP_LINES = ["呼…呼…Zzz", "好困哦…"]
WAKE_LINES = ["睡饱啦！", "谁叫我呀？", "起床气…才没有！"]
IDLE_LINES = ["好无聊呀~", "你在忙什么呀？", "陪我玩嘛~", "(｡•̀ᴗ-)✧"]
TYPING_LINES = ["你在打字呀~", "哒哒哒~", "键盘啪嗒啪嗒~", "我帮你按空格！"]
NO_KEY_LINES = ["(´•ω•̥`) 我还没接入 API… 在 pet_config.json 里填上 api_key 我就变聪明啦！",
                "想聊什么呀？先去 pet_config.json 配置 api_key 哦~"]


def res_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


# ---------- 调试日志 ----------
DEBUG = "--debug" in sys.argv
LOG_PATH = os.path.join(APP_DIR, "pet_debug.log")


def log(*args):
    if not DEBUG:
        return
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(" ".join(str(a) for a in args) + "\n")
    except Exception:
        pass


def _excepthook(t, v, tb):
    import traceback
    log("EXCEPTION:", t.__name__, str(v))
    log("".join(traceback.format_exception(t, v, tb)))


if DEBUG:
    sys.excepthook = _excepthook


# ---------- 全局键盘/鼠标低层钩子 ----------
class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", _POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


class InputMirror:
    """监听全局键盘按键与鼠标点击（只计数、不记录内容）。
    钩子回调在安装线程的消息循环中执行（Qt 主循环会泵消息），必须快速返回。
    用于桌宠对用户输入活动的同步反应：打字跟随摇摆、点击微反应。"""

    WH_KEYBOARD_LL = 13
    WH_MOUSE_LL = 14
    WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
    WM_LBUTTONDOWN, WM_RBUTTONDOWN = 0x0201, 0x0204

    def __init__(self):
        self.typing = 0
        self.clicks = 0
        self.last_key = 0.0
        self.last_click = 0.0
        self.last_click_pos = (0, 0)   # 物理像素
        self.installed = False
        self.kb_seen = False
        self.ms_seen = False
        self._kb_hook = None
        self._ms_hook = None
        self._kb_proc = None
        self._ms_proc = None

    def install(self):
        if self.installed or sys.platform != "win32":
            return self.installed
        try:
            user32 = ctypes.windll.user32
            HOOKPROC = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
            # 必须显式声明 restype/argtypes，否则句柄会被按 32 位截断
            user32.SetWindowsHookExW.restype = ctypes.c_void_p
            user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC,
                                                 ctypes.c_void_p, wintypes.DWORD]
            user32.UnhookWindowsHookEx.restype = wintypes.BOOL
            user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
            user32.CallNextHookEx.restype = ctypes.c_long
            user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                              wintypes.WPARAM, wintypes.LPARAM]
            kernel32 = ctypes.windll.kernel32
            kernel32.GetModuleHandleW.restype = ctypes.c_void_p
            hmod = kernel32.GetModuleHandleW(None)
            kb_proc = HOOKPROC(self._kb_cb)
            ms_proc = HOOKPROC(self._ms_cb)
            kb = user32.SetWindowsHookExW(self.WH_KEYBOARD_LL, kb_proc, hmod, 0)
            ms = user32.SetWindowsHookExW(self.WH_MOUSE_LL, ms_proc, hmod, 0)
            if kb and ms:
                self._kb_hook, self._ms_hook = kb, ms
                self._kb_proc, self._ms_proc = kb_proc, ms_proc
                self.installed = True
            else:
                if kb:
                    user32.UnhookWindowsHookEx(kb)
                if ms:
                    user32.UnhookWindowsHookEx(ms)
        except Exception as e:
            log("MIRROR-INSTALL-FAIL", str(e))
        log("MIRROR-INSTALLED", self.installed)
        return self.installed

    def uninstall(self):
        if not self.installed:
            return
        try:
            ctypes.windll.user32.UnhookWindowsHookEx(self._kb_hook)
            ctypes.windll.user32.UnhookWindowsHookEx(self._ms_hook)
        except Exception:
            pass
        self.installed = False

    def _kb_cb(self, nCode, wParam, lParam):
        if nCode == 0 and wParam in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN):
            self.typing += 1
            self.last_key = _t.monotonic()
            if not self.kb_seen:
                self.kb_seen = True
                log("HOOK-KB-FIRST")
        return ctypes.windll.user32.CallNextHookEx(self._kb_hook, nCode, wParam, lParam)

    def _ms_cb(self, nCode, wParam, lParam):
        if nCode == 0 and wParam in (self.WM_LBUTTONDOWN, self.WM_RBUTTONDOWN):
            self.clicks += 1
            self.last_click = _t.monotonic()
            if not self.ms_seen:
                self.ms_seen = True
                log("HOOK-MS-FIRST")
            try:
                st = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                self.last_click_pos = (st.pt.x, st.pt.y)
            except Exception:
                pass
        return ctypes.windll.user32.CallNextHookEx(self._ms_hook, nCode, wParam, lParam)

    def take(self):
        t = (self.typing, self.clicks, self.last_key, self.last_click, self.last_click_pos)
        self.typing = 0
        self.clicks = 0
        return t


# ---------- 开机自启（注册表 HKCU Run） ----------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "MoePetDesktopPet"


def exe_path():
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return ""


def autostart_enabled():
    if not exe_path():
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, RUN_VALUE)
        return os.path.normcase(val.strip('"')) == os.path.normcase(exe_path())
    except OSError:
        return False


def set_autostart(on):
    if not exe_path():
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ,
                                  '"' + exe_path() + '"')
            else:
                try:
                    winreg.DeleteValue(k, RUN_VALUE)
                except OSError:
                    pass
        return True
    except OSError as e:
        log("AUTOSTART-FAIL", str(e))
        return False


def image_to_datauri(path, max_dim=640):
    """读取本地图片，等比缩到 max_dim 内并转为 JPEG data URI（聊天发送用）"""
    try:
        from PIL import Image
        im = Image.open(path)
        im = im.convert("RGB")
        im.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        log("IMG-ENCODE-FAIL", str(e))
        return None


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    try:
        if os.path.exists(CFG_PATH):
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        else:
            save_cfg(cfg)
    except Exception:
        pass
    return cfg


def save_cfg(cfg):
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------- 对话气泡 ----------
class BubbleWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(300)
        self.label.setStyleSheet(
            "background:#fffdf8;border:1px solid #e6d9ff;border-radius:12px;"
            "color:#4a3a5a;font-family:'Microsoft YaHei UI';font-size:13px;"
            "padding:8px 12px;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.label)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    def show_text(self, text, ms, pet):
        self.label.setText(text)
        self.label.adjustSize()
        self.adjustSize()
        # 位于角色上方（不遮挡角色）
        vis_top = pet.y() - (pet.height() - pet.ih * pet.S) / 2
        x = pet.x() + (pet.width() - self.width()) // 2
        y = vis_top - self.height() - 6
        scr = pet.screen().availableGeometry() if pet.screen() else QApplication.primaryScreen().availableGeometry()
        x = max(scr.left() + 4, min(x, scr.right() - self.width() - 4))
        if y < scr.top() + 4:  # 屏幕顶部放不下就放脚底
            y = pet.y() + pet.height() + 6
        self.move(int(x), int(y))
        self.show()
        self.raise_()
        self.hide_timer.start(ms)


# ---------- 大小调整滑块窗 ----------
class SizeWindow(QWidget):
    def __init__(self, pet):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setStyleSheet(
            "QWidget#sizewin{background:#fffdf8;border:1px solid #e6d9ff;border-radius:10px;}"
            "QLabel{color:#4a3a5a;font-family:'Microsoft YaHei UI';}"
            "QPushButton{background:none;border:none;color:#9a8ab8;font-weight:bold;}"
            "QPushButton:hover{color:#6a5a88;}")
        self.setObjectName("sizewin")
        self.pet = pet
        self.pct = QLabel()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(18, 250)
        self.slider.setValue(int(pet.S * 100))
        self.slider.valueChanged.connect(self._on_slide)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.clicked.connect(self.close)
        row = QHBoxLayout()
        row.addWidget(QLabel("大小"))
        row.addWidget(self.slider, 1)
        row.addWidget(self.pct)
        row.addWidget(close_btn)
        self.setLayout(row)
        self._on_slide(self.slider.value())

    def _on_slide(self, v):
        self.pct.setText(f"{v}%")
        self.pet.set_scale(v / 100.0)

    def show_at(self, pet):
        self.adjustSize()
        x = pet.x() + (pet.width() - self.width()) // 2
        y = pet.y() + pet.height() + 8
        self.move(x, y)
        self.show()
        self.raise_()


# ---------- 聊天窗口（支持图片上传） ----------
class ChatWindow(QWidget):
    def __init__(self, pet):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.pet = pet
        self.image_path = None
        self._drag_off = None
        self.setObjectName("chatwin")
        self.setStyleSheet(
            "QWidget#chatwin{background:#fffdf8;border:1px solid #e6d9ff;border-radius:12px;}"
            "QTextBrowser{background:transparent;border:none;"
            "font-family:'Microsoft YaHei UI';font-size:13px;}"
            "QLineEdit{background:#ffffff;border:1px solid #e0d4f0;border-radius:8px;"
            "padding:6px 8px;font-family:'Microsoft YaHei UI';font-size:13px;}"
            "QPushButton{background:#f3ecff;border:1px solid #e0d4f0;border-radius:8px;"
            "padding:5px 12px;font-family:'Microsoft YaHei UI';color:#4a3a5a;}"
            "QPushButton:hover{background:#e6d9ff;}")
        self.setFixedSize(400, 500)

        self.header = QLabel(" 💬 陪我聊聊天")
        self.header.setStyleSheet(
            "background:#f3ecff;padding:7px 10px;font-weight:bold;color:#4a3a5a;")
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setStyleSheet(
            "background:transparent;border:none;font-weight:bold;color:#9a8ab8;")
        self.close_btn.clicked.connect(self.hide)
        hrow = QHBoxLayout()
        hrow.setContentsMargins(0, 0, 0, 0)
        hrow.setSpacing(0)
        hrow.addWidget(self.header, 1)
        hrow.addWidget(self.close_btn)

        self.view = QTextBrowser()
        self.view.document().setDefaultStyleSheet("img{max-width:220px;border-radius:8px;}")
        self.input = QLineEdit()
        self.input.setPlaceholderText("想说什么…（点「图片」可附带一张图）")
        self.input.returnPressed.connect(self._send)
        self.attach_btn = QPushButton("🖼 图片")
        self.attach_btn.clicked.connect(self._pick_image)
        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self._send)
        self.chip = QLabel("")
        self.chip.hide()
        self.chip.setStyleSheet(
            "background:#e6f7e6;border-radius:6px;padding:3px 8px;color:#3a6a3a;font-size:12px;")
        brow = QHBoxLayout()
        brow.setSpacing(6)
        brow.addWidget(self.input, 1)
        brow.addWidget(self.attach_btn)
        brow.addWidget(self.send_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 8)
        lay.setSpacing(6)
        lay.addLayout(hrow)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.chip)
        lay.addLayout(brow)
        self.append_system("你好呀~ 可以和我聊天，也可以点「图片」发图给我看！")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() <= self.header.height():
            self._drag_off = e.globalPosition().toPoint() - self.pos()
        else:
            self._drag_off = None
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_off is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_off)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_off = None
        super().mouseReleaseEvent(e)

    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "",
            "图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
        if not path:
            return
        self.image_path = path
        self.chip.setText("已选图: " + os.path.basename(path))
        self.chip.show()

    def _send(self):
        text = self.input.text().strip()
        img = self.image_path
        if not text and not img:
            return
        self.input.clear()
        self.image_path = None
        self.chip.hide()
        self.append_user(text, img)
        self.pet.chat_send(text, img)

    def append_user(self, text, image_path=None):
        uri = image_to_datauri(image_path) if image_path else None
        parts = []
        if text:
            parts.append(html.escape(text))
        if uri:
            parts.append(f'<img src="{uri}" width="220">')
        self._add("right", "#e3d9ff", "<br>".join(parts))

    def append_assistant(self, text):
        self._add("left", "#f5f0ff", html.escape(text))

    def append_system(self, text):
        self._add("center", "#f0f0f0", html.escape(text))

    def _add(self, align, bg, inner):
        self.view.append(
            f'<div style="text-align:{align};margin:5px 0;">'
            f'<span style="display:inline-block;background:{bg};border-radius:10px;'
            f'padding:6px 10px;max-width:75%;color:#4a3a5a;">{inner}</span></div>')

    def show_at(self, pet):
        scr = pet.screen().availableGeometry() if pet.screen() \
            else QApplication.primaryScreen().availableGeometry()
        x = pet.x() - self.width() - 14
        if x < scr.left() + 4:
            x = pet.x() + pet.width() + 14
        y = pet.y() + pet.height() - self.height()
        y = max(scr.top() + 4, min(y, scr.bottom() - self.height() - 4))
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()


# ---------- API 聊天工作线程 ----------
class ApiWorker(QObject):
    done = Signal(str)

    def ask(self, cfg, msgs):
        def work():
            try:
                base = (cfg.get("api_base") or "").strip().rstrip("/")
                if not base:
                    self.done.emit("__ERR__no base")
                    return
                url = base if base.endswith("/chat/completions") else base + "/chat/completions"
                payload = {
                    "model": cfg.get("model") or "deepseek-chat",
                    "messages": msgs,
                    "temperature": 1.0,
                    "max_tokens": 100,
                }
                req = urllib.request.Request(
                    url, data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json",
                             "Authorization": "Bearer " + (cfg.get("api_key") or "").strip()},
                    method="POST")
                with urllib.request.urlopen(req, timeout=45) as r:
                    data = json.loads(r.read().decode("utf-8"))
                self.done.emit(data["choices"][0]["message"]["content"].strip())
            except Exception as e:
                self.done.emit("__ERR__" + str(e))

        threading.Thread(target=work, daemon=True).start()


# ---------- 桌宠主窗口 ----------
class PetWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("桌宠")
        self.pix = QPixmap(res_path("pet.png"))
        self.iw, self.ih = self.pix.width(), self.pix.height()
        self.S = 0.30            # 缩放比例
        self.PAD = 1.45          # 窗口比图像多出的留白（防形变裁剪）
        self.setWindowIcon(QIcon(self.pix))

        self.mode = "idle"       # idle / drag / walk / follow / sleep
        self.sleeping = False
        self.walk = False
        self.follow = False
        self.always_top = True
        self.run_t = 0.0
        self.run_rot = 0.0
        self.run_bounce = 0.0
        self.fx = None           # 瞬时特效 {rot, sx, sy, ox, oy}
        self.fx_anims = []
        self.press_global = None
        self.drag_moved = False
        self.click_i = 0
        self.walk_target = None
        self.walk_pause_until = 0.0
        self.next_chatter = 0.0
        self.particles = []
        self.chat_hist = []
        self.api = None
        self.size_win = None
        self.chat_win = None
        self.mirror = InputMirror()
        self.sync_enabled = True
        self.mirror.install()
        self.look_rot = 0.0
        self.typing_energy = 0.0
        self.last_typing_say = 0.0
        self.bubble = BubbleWindow()

        self._apply_size()
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.right() - self.width() - 60,
                  scr.bottom() - self.height() - 20)

        # 主循环（姿态/移动/粒子）
        self.loop = QTimer(self)
        self.loop.timeout.connect(self._loop)
        self.loop.start(16)
        self.particle_timer = QTimer(self)
        self.particle_timer.timeout.connect(self._step_particles)
        # 睡觉时打呼
        self.zzz_timer = QTimer(self)
        self.zzz_timer.setSingleShot(True)
        self.zzz_timer.timeout.connect(self._zzz)

        self.show()
        log("INIT pos=", self.pos(), "size=", self.size(),
            "screen=", QApplication.primaryScreen().name(),
            "dpr=", self.devicePixelRatioF(),
            "flags=", int(self.windowFlags().value))
        self.hb = QTimer(self)
        self.hb.timeout.connect(lambda: log(
            "HEARTBEAT mode=", self.mode, "sleeping=", self.sleeping,
            "hook=", self.mirror.installed,
            "typingE=", round(self.typing_energy, 2),
            "look=", round(self.look_rot, 1)))
        self.hb.start(5000)
        QTimer.singleShot(700, lambda: self.say(random.choice(GREET_LINES), 2800))

    # ---------- 基础 ----------
    def _apply_size(self, keep_bottom=True):
        old_size = self.size()
        old_pos = self.pos()
        w = max(60, round(self.iw * self.S * self.PAD))
        h = max(60, round(self.ih * self.S * self.PAD))
        self.setFixedSize(w, h)
        if keep_bottom:
            self.move(old_pos.x() + (old_size.width() - w) // 2,
                      old_pos.y() + (old_size.height() - h))

    def set_scale(self, s):
        self.S = max(0.18, min(2.5, s))
        if self.sleeping:
            self._set_lying(True)
        else:
            self._apply_size(keep_bottom=True)

    def _set_lying(self, on):
        old_size = self.size()
        old_pos = self.pos()
        if on:
            w = round(self.ih * self.S * self.PAD)
            h = round(self.iw * self.S * self.PAD)
        else:
            w = round(self.iw * self.S * self.PAD)
            h = round(self.ih * self.S * self.PAD)
        self.setFixedSize(w, h)
        self.move(old_pos.x() + (old_size.width() - w) // 2,
                  old_pos.y() + (old_size.height() - h))

    def say(self, text, ms=3000):
        self.bubble.show_text(text, ms, self)

    # ---------- 粒子 ----------
    def spawn_particle(self, text, x, y, vx, vy, size=20, gravity=0.0, ttl=80):
        self.particles.append({"text": text, "x": x, "y": y,
                               "vx": vx, "vy": vy, "size": size,
                               "gravity": gravity, "ttl": ttl})
        if not self.particle_timer.isActive():
            self.particle_timer.start(33)

    def _step_particles(self):
        alive = []
        for p in self.particles:
            p["ttl"] -= 1
            p["x"] += p["vx"]
            p["y"] += p["vy"]
            p["vy"] += p["gravity"]
            if p["gravity"] and p["y"] > self.height() * 0.75:
                p["y"] = self.height() * 0.75
                p["vy"] = 0
            if p["ttl"] > 0:
                alive.append(p)
        self.particles = alive
        if not self.particles:
            self.particle_timer.stop()
        self.update()

    # ---------- 主循环 ----------
    def _loop(self):
        self.run_t += 0.016
        target_rot, target_bounce = 0.0, 0.0
        if self.mode == "walk" and self._walk_step():
            target_rot = math.sin(self.run_t * 11) * 9
            target_bounce = abs(math.sin(self.run_t * 11)) * 5
        elif self.mode == "follow" and self._follow_step():
            target_rot = math.sin(self.run_t * 12) * 9
            target_bounce = abs(math.sin(self.run_t * 12)) * 5
        elif self.mode == "drag":
            target_rot = math.sin(self.run_t * 12) * 10
            target_bounce = abs(math.sin(self.run_t * 12)) * 6
        self.run_rot += (target_rot - self.run_rot) * 0.35
        self.run_bounce += (target_bounce - self.run_bounce) * 0.35

        now = time_mono()
        self._mirror_step(now)
        if now >= self.next_chatter and not self.sleeping and self.mode == "idle":
            self.next_chatter = now + random.uniform(28, 60)
            if random.random() < 0.45:
                self.say(random.choice(IDLE_LINES), 2600)
        self.update()

    def _mirror_step(self, now):
        """键盘/鼠标活动同步：打字跟随摇摆、视线跟随光标、点击微反应"""
        if not self.mirror.installed:
            return
        typing, clicks, last_key, last_click, click_pos = self.mirror.take()

        # 1) 打字：快速小摇摆 + 键盘 emoji 粒子 + 偶尔说话
        typing_active = typing > 0 and (now - last_key) < 0.5
        if typing_active and not self.sleeping:
            self.typing_energy = min(1.0, self.typing_energy + 0.22)
            if random.random() < 0.05:
                self.spawn_particle("⌨️",
                                    self.width() / 2 + random.randrange(-30, 30),
                                    self.height() * 0.15,
                                    random.uniform(-0.4, 0.4), -0.9, 16, ttl=55)
            if now - self.last_typing_say > 4.0 and random.random() < 0.5:
                self.last_typing_say = now
                self.say(random.choice(TYPING_LINES), 2200)
        else:
            self.typing_energy = max(0.0, self.typing_energy - 0.05)

        # 2) 视线跟随光标：空闲时向光标所在方向倾斜
        cur = QCursor.pos()
        if self.mode == "idle" and not self.sleeping and not self._busy():
            dx = cur.x() - (self.x() + self.width() / 2)
            target = max(-1.0, min(1.0, dx / (self.width() * 1.6))) * 10.0
            self.look_rot += (target - self.look_rot) * 0.08
        else:
            self.look_rot += (0.0 - self.look_rot) * 0.08

        # 3) 在桌宠之外点击鼠标：轻微压扁回应（点桌宠本身走正常交互）
        if clicks > 0 and (now - last_click) < 0.3:
            dpr = max(1.0, self.devicePixelRatioF())
            inside = self.frameGeometry().contains(
                QPoint(round(click_pos[0] / dpr), round(click_pos[1] / dpr)))
            if (not inside and not self.sleeping
                    and self.mode == "idle" and not self._busy()):
                def step(v):
                    sy = 1 - 0.05 * math.sin(v * math.pi)
                    self.fx = {"sy": sy, "sx": 1 + (1 - sy) * 0.8}
                self._fx_anim(160, step)
                if random.random() < 0.06:
                    self.say("你点哪里呢？", 2000)

    def _walk_step(self):
        now = time_mono()
        scr = self.screen().availableGeometry() if self.screen() \
            else QApplication.primaryScreen().availableGeometry()
        margin = 80
        if now < self.walk_pause_until:
            return False
        if self.walk_target is None:
            self.walk_target = QPoint(random.randint(scr.left() + margin, scr.right() - margin),
                                      random.randint(scr.top() + margin, scr.bottom() - margin))
        cur = self.pos()
        dx = self.walk_target.x() - cur.x()
        dy = self.walk_target.y() - cur.y()
        dist = math.hypot(dx, dy)
        if dist < 10:
            self.walk_pause_until = now + random.uniform(0.5, 2.2)
            self.walk_target = None
            return False
        step = min(dist, 3.5 + 7 * self.S)
        self.move(cur.x() + int(dx / dist * step), cur.y() + int(dy / dist * step))
        return True

    def _follow_step(self):
        cur = QCursor.pos()
        c = self.pos() + QPoint(self.width() // 2, self.height() // 2)
        dx = cur.x() - c.x()
        dy = cur.y() - c.y()
        dist = math.hypot(dx, dy)
        if dist < 36:
            return False
        step = min(dist, max(10, dist * 0.12))
        self.move(self.x() + int(dx / dist * step), self.y() + int(dy / dist * step))
        return True

    # ---------- 特效 ----------
    def _fx_anim(self, dur, step_fn, done=None):
        a = QVariantAnimation(self)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setDuration(dur)
        a.setEasingCurve(QEasingCurve.Type.InOutQuad)
        a.valueChanged.connect(step_fn)
        a.finished.connect(lambda: self._fx_finished(a, done))
        self.fx_anims.append(a)
        a.start()
        return a

    def _fx_finished(self, a, done):
        if a in self.fx_anims:
            self.fx_anims.remove(a)
        if not self.fx_anims:
            self.fx = None
        if done:
            done()

    def _busy(self):
        return bool(self.fx_anims)

    def do_jump(self):
        if self._busy():
            return
        log("JUMP-START")
        base = self.pos()
        h = round(self.ih * self.S * 0.20)
        up = QVariantAnimation(self)
        up.setStartValue(0.0)
        up.setEndValue(1.0)
        up.setDuration(240)
        up.setEasingCurve(QEasingCurve.Type.OutQuad)
        down = QVariantAnimation(self)
        down.setStartValue(1.0)
        down.setEndValue(0.0)
        down.setDuration(210)
        down.setEasingCurve(QEasingCurve.Type.InQuad)
        self.fx_anims.append(up)
        self.fx_anims.append(down)
        up.valueChanged.connect(lambda v: self.move(base.x(), base.y() - int(h * v)))
        down.valueChanged.connect(lambda v: self.move(base.x(), base.y() - int(h * v)))
        down.finished.connect(lambda: self._fx_finished(down, self._land))
        up.finished.connect(lambda: self._fx_finished(up, None))
        up.start()

    def _land(self):
        self.say(random.choice(JUMP_LINES), 2400)
        def step(v):
            sy = 1 - 0.12 * math.sin(v * math.pi)
            self.fx = {"sy": sy, "sx": 1 + (1 - sy) * 0.8}
        self._fx_anim(260, step)

    def do_squash(self):
        if self._busy():
            return
        log("SQUASH-START")
        def step(v):
            sy = 1 - 0.30 * math.sin(v * math.pi) + 0.05 * math.sin(v * 4 * math.pi)
            self.fx = {"sx": 1 + (1 - sy) * 0.7, "sy": sy}
        self._fx_anim(680, step, done=lambda: self.say(random.choice(SQUASH_LINES), 2400))

    def do_shake(self):
        if self._busy():
            return
        log("SHAKE-START")
        def step(v):
            self.fx = {"rot": math.sin(v * math.pi * 8) * 12 * (1 - 0.3 * v),
                       "ox": math.sin(v * math.pi * 8) * 6}
        self._fx_anim(640, step, done=lambda: self.say(random.choice(SHAKE_LINES), 2400))

    # ---------- 互动动作 ----------
    def do_pat(self):
        if self.sleeping:
            self.wake(line=False)
        cx = self.width() // 2
        top = self.height() * 0.18
        self.spawn_particle("💗", cx - 26, top, -0.6, -1.8, 22, ttl=70)
        self.spawn_particle("💕", cx + 8, top - 10, 0.5, -1.5, 24, ttl=80)
        self.spawn_particle("✨", cx - 8, top - 20, 0.2, -1.2, 18, ttl=60)
        def step(v):
            sy = 1 - 0.06 * abs(math.sin(v * math.pi * 4))
            self.fx = {"sy": sy, "sx": 1 + (1 - sy) * 0.7,
                       "rot": -5 + 10 * abs(math.sin(v * math.pi * 2))}
        self._fx_anim(900, step, done=lambda: self.say(random.choice(PAT_LINES), 2600))

    def do_feed(self):
        if self.sleeping:
            self.wake(line=False)
        foods = ["🍙", "🍰", "🍓", "🍞", "🍭", "🧁"]
        self.spawn_particle(random.choice(foods),
                            self.width() / 2 - 30 + random.randrange(-40, 40),
                            self.height() * 0.05, 0, 0.4, 24, gravity=0.9, ttl=110)
        def step(v):
            sy = 1 - 0.08 * abs(math.sin(v * math.pi * 3))
            self.fx = {"sy": sy, "sx": 1 + (1 - sy) * 0.7}
        self._fx_anim(1100, step, done=lambda: self.say(random.choice(FEED_LINES), 2800))

    def start_walk(self):
        if self.sleeping:
            self.wake(line=False)
        self.walk = True
        self.follow = False
        self.mode = "walk"
        self.walk_target = None
        self.say(random.choice(WALK_LINES), 2200)

    def stop_walk(self, quiet=False):
        self.walk = False
        if self.mode == "walk":
            self.mode = "idle"
        if not quiet:
            self.say("到家啦~", 2000)

    def toggle_follow(self):
        if self.sleeping:
            self.wake(line=False)
        if self.follow:
            self.follow = False
            self.mode = "idle"
            self.say("不追啦，休息一下~", 2200)
        else:
            self.follow = True
            self.walk = False
            self.mode = "follow"
            self.say(random.choice(FOLLOW_LINES), 2200)

    def start_sleep(self):
        if self.sleeping:
            return
        self.sleeping = True
        self.walk = False
        self.follow = False
        self.mode = "sleep"
        self._set_lying(True)
        self.say(random.choice(SLEEP_LINES), 2200)
        self.zzz_timer.start(2600)

    def _zzz(self):
        if self.sleeping:
            self.say("Zzz…", 1400)
            self.zzz_timer.start(2600)

    def wake(self, line=True):
        if not self.sleeping:
            return
        self.sleeping = False
        self.mode = "idle"
        self.zzz_timer.stop()
        self._set_lying(False)
        self.do_squash()
        if line:
            self.say(random.choice(WAKE_LINES), 2400)

    def toggle_top(self):
        self.always_top = not self.always_top
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.always_top)
        self.show()
        self.say("置顶已开启" if self.always_top else "置顶已关闭", 1800)

    # ---------- 聊天 ----------
    def do_chat(self):
        if self.sleeping:
            self.wake(line=False)
        if self.chat_win is None:
            self.chat_win = ChatWindow(self)
        self.chat_win.show_at(self)

    def chat_send(self, text, image_path=None):
        """发送聊天（可带图片，OpenAI 兼容多模态格式）"""
        cfg = load_cfg()
        if not self.chat_hist:
            self.chat_hist = [{"role": "system",
                               "content": cfg.get("persona") or DEFAULT_CFG["persona"]}]
        uri = image_to_datauri(image_path) if image_path else None
        if uri:
            content = [{"type": "text", "text": text or "看看这张图~"},
                       {"type": "image_url", "image_url": {"url": uri}}]
        else:
            content = text
        self.chat_hist.append({"role": "user", "content": content})
        if not (cfg.get("api_key") or "").strip():
            self._chat_reply(random.choice(NO_KEY_LINES))
            return
        self.say("让我想想…", 1600)
        cap = int(cfg.get("max_history") or 10)
        msgs = self.chat_hist if len(self.chat_hist) <= cap + 1 \
            else [self.chat_hist[0]] + self.chat_hist[-(cap):]
        if self.api is None:
            self.api = ApiWorker()
            self.api.done.connect(self._on_chat_reply)
        self.api.ask(cfg, msgs)

    def _on_chat_reply(self, text):
        if text.startswith("__ERR__"):
            self._chat_reply("呜…聊不上天，检查一下 pet_config.json 的 api 配置哦。"
                             "如果发的是图片，需要接口支持视觉（如 glm-4v / qwen-vl / gpt-4o）。")
            return
        self._chat_reply(text)

    def _chat_reply(self, text):
        self.chat_hist.append({"role": "assistant", "content": text})
        if self.chat_win is not None:
            self.chat_win.append_assistant(text)
        self.say(text[:130], 6500)

    # ---------- 键盘鼠标同步 / 开机自启 ----------
    def toggle_sync(self):
        self.sync_enabled = not self.sync_enabled
        if self.sync_enabled:
            self.mirror.install()
        else:
            self.mirror.uninstall()
        self.say("键盘鼠标同步已开启" if self.sync_enabled else "键盘鼠标同步已关闭", 2000)

    def toggle_autostart(self):
        on = not autostart_enabled()
        ok = set_autostart(on)
        if ok:
            self.say("已开启开机自启，下次开机我会自己跑出来~" if on else "已关闭开机自启", 2600)
        else:
            self.say("设置开机自启失败…（打包成 EXE 后可用）", 3200)

    # ---------- 鼠标事件 ----------
    def enterEvent(self, e):
        log("ENTER")
        super().enterEvent(e)

    def mousePressEvent(self, e):
        log("PRESS btn=", int(e.button().value), "pos=", e.globalPosition().toPoint())
        if e.button() == Qt.MouseButton.LeftButton:
            self.press_global = e.globalPosition().toPoint()
            self.drag_moved = False
            if self.sleeping:
                self.wake(line=True)
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and self.press_global is not None:
            cur = e.globalPosition().toPoint()
            d = cur - self.press_global
            if not self.drag_moved and d.manhattanLength() > 6:
                self.drag_moved = True
                self.mode = "drag"
                log("DRAG-START")
            if self.drag_moved:
                self.move(self.pos() + d)
                self.press_global = cur
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        log("RELEASE btn=", int(e.button().value), "was_drag=", self.drag_moved)
        if e.button() == Qt.MouseButton.LeftButton and self.press_global is not None:
            was_drag = self.drag_moved
            self.press_global = None
            self.drag_moved = False
            if was_drag:
                if self.mode == "drag":
                    self.mode = "follow" if self.follow else ("walk" if self.walk else "idle")
                if random.random() < 0.3:
                    self.say(random.choice(DRAG_LINES), 2200)
            elif not self.sleeping:
                self._on_click()
        super().mouseReleaseEvent(e)

    def _on_click(self):
        log("CLICK walk=", self.walk, "busy=", self._busy())
        if self.walk:
            self.stop_walk()
            return
        if self._busy():
            return
        actions = [self.do_jump, self.do_squash, self.do_shake]
        actions[self.click_i % len(actions)]()
        self.click_i += 1

    def wheelEvent(self, e):
        log("WHEEL delta=", e.angleDelta().y())
        delta = e.angleDelta().y()
        if delta == 0:
            return
        self.set_scale(self.S * (1.12 if delta > 0 else 1 / 1.12))

    # ---------- 右键菜单 ----------
    def contextMenuEvent(self, e):
        log("CONTEXT-MENU")
        m = QMenu(self)
        a_chat = m.addAction("陪我聊聊天")
        a_pat = m.addAction("摸摸头")
        a_feed = m.addAction("喂吃的")
        m.addSeparator()
        a_walk = m.addAction("让她走路")
        a_walk.setCheckable(True)
        a_walk.setChecked(self.walk)
        a_sleep = m.addAction("让她睡觉")
        a_sleep.setCheckable(True)
        a_sleep.setChecked(self.sleeping)
        a_follow = m.addAction("跟随鼠标")
        a_follow.setCheckable(True)
        a_follow.setChecked(self.follow)
        m.addSeparator()
        a_size = m.addAction("调整大小")
        a_sync = m.addAction("跟随键盘鼠标")
        a_sync.setCheckable(True)
        a_sync.setChecked(self.sync_enabled)
        a_auto = m.addAction("开机自启")
        a_auto.setCheckable(True)
        a_auto.setChecked(autostart_enabled())
        if not getattr(sys, "frozen", False):
            a_auto.setEnabled(False)
            a_auto.setChecked(False)
        a_top = m.addAction("置顶开关")
        a_top.setCheckable(True)
        a_top.setChecked(self.always_top)
        m.addSeparator()
        a_quit = m.addAction("退出程序")
        act = m.exec(QCursor.pos())
        log("MENU-RESULT=", None if act is None else act.text())
        if act is None:
            return
        if act == a_chat:
            self.do_chat()
        elif act == a_pat:
            self.do_pat()
        elif act == a_feed:
            self.do_feed()
        elif act == a_walk:
            self.start_walk() if not self.walk else self.stop_walk()
        elif act == a_sleep:
            self.start_sleep() if not self.sleeping else self.wake()
        elif act == a_follow:
            self.toggle_follow()
        elif act == a_size:
            if self.size_win is None:
                self.size_win = SizeWindow(self)
            self.size_win.show_at(self)
        elif act == a_sync:
            self.toggle_sync()
        elif act == a_auto:
            self.toggle_autostart()
        elif act == a_top:
            self.toggle_top()
        elif act == a_quit:
            QApplication.quit()

    def closeEvent(self, e):
        self.mirror.uninstall()
        super().closeEvent(e)

    # ---------- 绘制 ----------
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        S = self.S
        breath = 1.0 + 0.015 * math.sin(self.run_t * 2.0)
        rot, sx, sy, ox, oy = 0.0, 1.0, 1.0, 0.0, 0.0
        if self.sleeping:
            rot = 90.0
            breath = 1.0 + 0.03 * math.sin(self.run_t * 1.6)
        elif self.mode in ("walk", "follow", "drag"):
            rot = self.run_rot
            oy = -self.run_bounce
        # 视线跟随光标倾斜 + 打字快速摇摆
        rot += self.look_rot + math.sin(self.run_t * 26.0) * 7.0 * self.typing_energy
        fx = self.fx
        if fx:
            rot += fx.get("rot", 0.0)
            sx *= fx.get("sx", 1.0)
            sy *= fx.get("sy", 1.0)
            ox += fx.get("ox", 0.0)
            oy += fx.get("oy", 0.0)
        iw, ih = self.iw * S, self.ih * S
        bottom = self.height() - (self.height() - ih) / 2
        p.translate(self.width() / 2 + ox, bottom + oy)
        p.rotate(rot)
        p.scale(sx * breath, sy * breath)
        p.drawPixmap(QRect(round(-iw / 2), round(-ih), round(iw), round(ih)), self.pix)
        p.resetTransform()
        for pt in self.particles:
            a = max(0.0, min(1.0, pt["ttl"] / 28))
            f = QFont("Segoe UI Emoji")
            f.setPixelSize(pt["size"])
            p.setFont(f)
            p.setOpacity(a)
            p.drawText(QPoint(round(pt["x"]), round(pt["y"])), pt["text"])
        p.setOpacity(1.0)
        p.end()


import time as _t


def time_mono():
    return _t.monotonic()


def force_dpi_aware():
    """强制进程为 Per-Monitor V2 DPI 感知。
    若启动本程序的父进程（终端/宿主/计划任务）是 DPI 不感知的，
    Windows 会把窗口坐标原样当作物理像素，而 Qt 内部按高 DPI 计算，
    导致窗口错位、渲染裁剪、鼠标命中测试全部失效。"""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:
        pass


def run_autotest(pet):
    """进程内自动交互测试（--autoclick）：验证点击/拖动/滚轮/右键菜单全链路"""
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QWheelEvent, QContextMenuEvent
    from PySide6.QtCore import QPointF

    c = QPoint(pet.width() // 2, int(pet.height() * 0.6))

    def click(pt):
        QTest.mouseClick(pet, Qt.MouseButton.LeftButton, pos=pt)

    def drag_test():
        start = QPoint(pet.width() // 2, pet.height() // 2)
        QTest.mousePress(pet, Qt.MouseButton.LeftButton, pos=start)
        for i in range(1, 9):
            QTest.mouseMove(pet, pos=start + QPoint(-8 * i, 0), delay=30)
        QTest.mouseRelease(pet, Qt.MouseButton.LeftButton, pos=start + QPoint(-64, 0))

    def wheel_test():
        ev = QWheelEvent(QPointF(c), QPointF(pet.mapToGlobal(c)),
                         QPoint(), QPoint(0, 120),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                         Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(pet, ev)

    def menu_test():
        QTimer.singleShot(1200, lambda: (
            QApplication.activePopupWidget() and QApplication.activePopupWidget().close()))
        ev = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, c, pet.mapToGlobal(c))
        QApplication.sendEvent(pet, ev)

    def mirror_test():
        pet.mirror._kb_cb(0, pet.mirror.WM_KEYDOWN, 0)      # 模拟全局按键回调
        pet.mirror._ms_cb(0, pet.mirror.WM_LBUTTONDOWN, 0)  # 模拟全局点击回调
        t = pet.mirror.take()
        log("MIRROR-TEST typing=", t[0], "clicks=", t[1],
            "hook=", pet.mirror.installed)

    def chat_test():
        pet.do_chat()
        log("CHAT-WIN-VISIBLE", pet.chat_win is not None and pet.chat_win.isVisible())
        if pet.chat_win is not None:
            pet.chat_win.input.setText("测试消息")
            pet.chat_win._send()
            # 图片消息：临时生成一张测试图，验证图片编码与多模态发送
            try:
                import tempfile
                from PIL import Image as _Im
                img_path = os.path.join(tempfile.gettempdir(), "moepet_test.png")
                _Im.new("RGB", (200, 150), (200, 120, 255)).save(img_path)
                pet.chat_win.append_user("图片测试", img_path)
                log("IMG-DATAURI-OK", image_to_datauri(img_path) is not None)
                pet.chat_send("图片测试", img_path)
            except Exception as e:
                log("CHAT-IMG-FAIL", str(e))
            plain = pet.chat_win.view.toPlainText()
            log("CHAT-REPLY=", plain[-60:].replace("\n", " | "))
            pet.chat_win.hide()

    def autostart_test():
        if getattr(sys, "frozen", False):
            r1 = set_autostart(True)
            r2 = autostart_enabled()
            r3 = set_autostart(False)
            log("AUTOSTART-TEST set=", r1, "check=", r2, "unset=", r3)
        else:
            log("AUTOSTART-TEST skipped (not frozen)")

    def plan():
        log("AUTOTEST-START pos=", pet.pos(), "size=", pet.size(),
            "dpr=", pet.devicePixelRatioF(), "hook=", pet.mirror.installed)
        click(c)                                            # 跳跃
        QTimer.singleShot(1300, drag_test)                  # 拖动
        QTimer.singleShot(2800, lambda: click(c))           # 压扁回弹
        QTimer.singleShot(4300, wheel_test)                 # 滚轮放大
        QTimer.singleShot(5600, menu_test)                  # 右键菜单
        QTimer.singleShot(6800, mirror_test)                # 键盘/鼠标钩子
        QTimer.singleShot(8000, chat_test)                  # 聊天窗口
        QTimer.singleShot(9200, autostart_test)             # 开机自启
        QTimer.singleShot(10200, lambda: (log("AUTOTEST-DONE"), QApplication.quit()))

    QTimer.singleShot(2500, plan)


def main():
    force_dpi_aware()
    log("===== START =====", sys.version, "frozen=", getattr(sys, "frozen", False))
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    log("APP-READY platform=", app.platformName(),
        "screens=", [s.name() for s in app.screens()])
    app.setQuitOnLastWindowClosed(True)
    pet = PetWindow()
    if "--selftest" in sys.argv:
        print("SELFTEST OK; config:", CFG_PATH, "exists:", os.path.exists(CFG_PATH))
        QTimer.singleShot(1600, app.quit)
    if "--autoclick" in sys.argv:
        run_autotest(pet)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
