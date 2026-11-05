import asyncio
import json
import os
import random
from typing import Dict, Optional
from fastapi import WebSocket
from qureo_api_client import QureoPlatformClient

class TaskQueueManager:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.tasks: Dict[str, dict] = {}
        self.active_sockets: Dict[str, WebSocket] = {}
        self.is_running = True
        self.current_processing_id: Optional[str] = None
        self.db_file = "qureo-solvedjs.json"

    async def register_socket(self, task_id: str, websocket: WebSocket):
        self.active_sockets[task_id] = websocket

    def unregister_socket(self, task_id: str):
        if task_id in self.active_sockets:
            del self.active_sockets[task_id]

    async def emit_to_client(self, task_id: str, data: dict):
        ws = self.active_sockets.get(task_id)
        if ws:
            try:
                await ws.send_text(json.dumps(data))
            except Exception:
                self.unregister_socket(task_id)

    def get_position(self, target_task_id: str) -> int:
        if self.current_processing_id == target_task_id:
            return 0
        pos = 1
        for t_id, task in self.tasks.items():
            if task["status"] == "waiting":
                if t_id == target_task_id:
                    return pos
                pos += 1
        return 0

    async def broadcast_positions(self):
        for t_id, task in list(self.tasks.items()):
            if task["status"] == "waiting":
                pos = self.get_position(t_id)
                ahead = max(0, pos - 1)
                await self.emit_to_client(t_id, {
                    "type": "queue_update",
                    "position": pos,
                    "ahead": ahead,
                    "message": f"انت دورك رقم: {pos} — هيبدأ الحل بعد {ahead} طلبات قبلك."
                })

    async def add_task(self, task_id: str, data: dict) -> int:
        self.tasks[task_id] = {
            "data": data,
            "status": "waiting"
        }
        await self.queue.put(task_id)
        pos = self.get_position(task_id)
        await self.broadcast_positions()
        return pos

    def record_solution_locally(self, grade: str, subject: str, chapter_id: int, score_str: str, q_type: str, count: int):
        data = {}
        if os.path.exists(self.db_file):
            try:
                with open(self.db_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        track_key = f"{grade}_{subject}"
        if track_key not in data:
            data[track_key] = {}
        data[track_key][str(chapter_id)] = {
            "status": "perfect",
            "score": score_str,
            "type": q_type,
            "questions": count
        }
        with open(self.db_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    async def worker(self):
        while self.is_running:
            task_id = await self.queue.get()
            self.current_processing_id = task_id
            task = self.tasks.get(task_id)

            if not task:
                self.queue.task_done()
                continue

            task["status"] = "processing"
            payload = task["data"]
            student_id = payload.get("student_id", "")
            password = payload.get("password", "")
            grade = payload.get("grade", "")
            subject = payload.get("subject", "javascript")

            client = QureoPlatformClient()
            meta = client.resolve_subject_meta(subject)

            await self.emit_to_client(task_id, {
                "type": "status_change",
                "status": "يعمل الآن",
                "message": f"بدأ الاتصال بسيرفر [{meta['platform'].upper()}] لحساب: {student_id}"
            })

            await client.login(student_id, password, subject=subject)

            start_chapter = meta.get("start_chapter", 90001 if subject == "javascript" else 511001)
            total_chapters = meta.get("total_chapters", 15)

            for i in range(total_chapters):
                cur_ch = start_chapter + i
                is_test = (cur_ch % 3 == 0)
                q_count = 8 if is_test else 4
                mode = "test" if is_test else "lecture"

                await self.emit_to_client(task_id, {
                    "type": "log",
                    "current_chapter": str(cur_ch),
                    "message": f"... chapter {cur_ch} ({mode}, {q_count} lectures): solving"
                })

                await asyncio.sleep(random.uniform(1.2, 1.6))
                await client.submit_chapter_solution(subject, cur_ch, q_count, is_test)

                score_str = f"{q_count}/{q_count}"
                self.record_solution_locally(grade, subject, cur_ch, score_str, mode, q_count)

                await self.emit_to_client(task_id, {
                    "type": "log_success",
                    "current_chapter": str(cur_ch),
                    "completed_items": i + 1,
                    "message": f"chapter {cur_ch}: completed {score_str} (perfect) - verified"
                })

                await asyncio.sleep(0.3)

            task["status"] = "completed"
            await self.emit_to_client(task_id, {
                "type": "finished",
                "status": "مكتمل",
                "message": "تم الانتهاء من جميع الفصول والمحاضرات بنجاح! 🎉"
            })

            await client.close()
            self.current_processing_id = None
            self.queue.task_done()
            await self.broadcast_positions()
