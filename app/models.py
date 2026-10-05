import json
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Recording(Base):
    """One finished test-automation run: its video plus what the run itself reported.

    `slug` is the id in the shared link (/r/<slug>). It is random so links posted to
    Lark can't be guessed by counting. All datetimes are naive UTC.
    """

    __tablename__ = "recordings"

    id = Column(Integer, primary_key=True)
    slug = Column(String(32), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    title = Column(String(255))  # the Lark card title, e.g. "Web/PC automatic testing for provider: JILI"
    variant = Column(String(16))  # desktop / phone / h5
    status = Column(String(16), nullable=False, default="unknown")  # pass / fail / warn / unknown
    report_text = Column(Text)  # the card body: Duration, the ✅/❌ checklist, the summary
    summary = Column(Text)
    facts_json = Column(Text)  # last_run_results.json "facts" as sent by the automation
    duration_seconds = Column(Float)

    video_key = Column(Text)  # storage key, e.g. "recordings/<slug>.mp4"
    # Frames pulled from the video for the AI: pending / ready / failed.
    frames_status = Column(String(16), nullable=False, default="pending")
    frames_error = Column(Text)
    frame_count = Column(Integer, default=0)
    frame_interval = Column(Float)  # seconds between frames
    video_seconds = Column(Float)  # real length of the video file (ffprobe)

    @property
    def facts(self) -> dict:
        try:
            return json.loads(self.facts_json or "{}")
        except ValueError:
            return {}
