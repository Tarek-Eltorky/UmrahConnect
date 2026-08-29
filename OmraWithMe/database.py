from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean, text, Index, UniqueConstraint, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os

_DB_DIR = os.path.dirname(os.path.abspath(__file__))
# OMRA_DB_PATH lets tests / deployments point at a different SQLite file
_DB_PATH = os.environ.get("OMRA_DB_PATH", os.path.join(_DB_DIR, "omrawithme.db"))
SQLALCHEMY_DATABASE_URL = f"sqlite:///{_DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

# Enforce SQLite foreign-key constraints (off by default)
@event.listens_for(engine, "connect")
def _sqlite_fk_on(dbapi_connection, _conn_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    full_name = Column(String)
    national_id = Column(String, index=True)  # unique enforced at app level (allow null/duplicates only when empty)
    facebook_id = Column(String)
    facebook_name = Column(String)
    phone = Column(String)
    passport_number = Column(String)
    passport_expiry = Column(String)  # YYYY-MM-DD
    profile_image = Column(String, default="/static/images/default-avatar.png")
    created_at = Column(DateTime, default=datetime.utcnow)
    is_verified = Column(Boolean, default=False)
    is_deleted = Column(Boolean, default=False, index=True)
    is_admin = Column(Boolean, default=False)
    locale = Column(String, default="en")  # 'en' | 'ar'
    
    # Relationships
    announcements = relationship("Announcement", back_populates="creator")
    join_requests = relationship("JoinRequest", back_populates="requester", foreign_keys="JoinRequest.requester_id")
    comments = relationship("Comment", back_populates="author")

class Announcement(Base):
    __tablename__ = "announcements"
    
    id = Column(Integer, primary_key=True, index=True)
    creator_id = Column(Integer, ForeignKey("users.id"))
    title = Column(String)
    location = Column(String)  # Makkah, Madinah, Makkah/Madinah
    
    # Travel dates
    departure_date = Column(DateTime)   # auto-computed = min of city check-ins
    return_date    = Column(DateTime)   # auto-computed = max of city check-outs
    # City-specific dates (for Makkah/Madinah trips)
    makkah_checkin   = Column(DateTime, nullable=True)
    makkah_checkout  = Column(DateTime, nullable=True)
    madinah_checkin  = Column(DateTime, nullable=True)
    madinah_checkout = Column(DateTime, nullable=True)
    
    # Hotel details
    hotel_name = Column(String)           # Makkah hotel (or only hotel for Makkah-only trips)
    hotel_stars = Column(Integer, default=3)
    hotel_name_madinah = Column(String)   # Madinah hotel (only for Makkah/Madinah trips)
    hotel_stars_madinah = Column(Integer, default=3)
    room_type = Column(String)  # Single, Double, Triple, Quad
    people_per_room = Column(Integer)
    total_rooms_available = Column(Integer)
    
    # Budget
    budget_per_person = Column(Float)  # in EGP
    includes_transport = Column(Boolean, default=False)
    includes_meals = Column(Boolean, default=False)
    
    # Additional info
    description = Column(Text)
    requirements = Column(Text)
    
    # Status
    is_active = Column(Boolean, default=True, index=True)
    is_deleted = Column(Boolean, default=False, index=True)
    spots_filled = Column(Integer, default=0)
    max_participants = Column(Integer)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    creator = relationship("User", back_populates="announcements")
    join_requests = relationship("JoinRequest", back_populates="announcement")
    comments = relationship("Comment", back_populates="announcement")

class JoinRequest(Base):
    __tablename__ = "join_requests"
    
    id = Column(Integer, primary_key=True, index=True)
    announcement_id = Column(Integer, ForeignKey("announcements.id"), index=True)
    requester_id = Column(Integer, ForeignKey("users.id"), index=True)
    
    message = Column(Text)
    num_people = Column(Integer, default=1)
    status = Column(String, default="pending", index=True)  # pending, accepted, rejected
    
    response_message = Column(Text)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    responded_at = Column(DateTime)
    
    # Relationships
    announcement = relationship("Announcement", back_populates="join_requests")
    requester = relationship("User", back_populates="join_requests", foreign_keys=[requester_id])

class Comment(Base):
    __tablename__ = "comments"
    
    id = Column(Integer, primary_key=True, index=True)
    announcement_id = Column(Integer, ForeignKey("announcements.id"))
    author_id = Column(Integer, ForeignKey("users.id"))
    
    content = Column(Text)
    is_suggestion = Column(Boolean, default=False)  # True if it's a tweak suggestion
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    announcement = relationship("Announcement", back_populates="comments")
    author = relationship("User", back_populates="comments")

class Notification(Base):
    __tablename__ = "notifications"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    message = Column(String)
    link = Column(String)
    notif_type = Column(String)  # new_request, accepted, rejected, comment, new_message
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", backref="notifications")

class Message(Base):
    __tablename__ = "messages"

    id              = Column(Integer, primary_key=True, index=True)
    join_request_id = Column(Integer, ForeignKey("join_requests.id"), index=True)
    sender_id       = Column(Integer, ForeignKey("users.id"), index=True)
    content         = Column(Text)
    created_at      = Column(DateTime, default=datetime.utcnow)
    is_read         = Column(Boolean, default=False)

    sender       = relationship("User")
    join_request = relationship("JoinRequest")

class BlockedUser(Base):
    __tablename__ = "blocked_users"
    id          = Column(Integer, primary_key=True, index=True)
    blocker_id  = Column(Integer, ForeignKey("users.id"), index=True)
    blocked_id  = Column(Integer, ForeignKey("users.id"), index=True)
    reason      = Column(String, default="")
    created_at  = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id", name="uq_block_pair"),)

class UserReport(Base):
    __tablename__ = "user_reports"
    id           = Column(Integer, primary_key=True, index=True)
    reporter_id  = Column(Integer, ForeignKey("users.id"), index=True)
    reported_id  = Column(Integer, ForeignKey("users.id"), index=True)
    reason       = Column(String)
    details      = Column(Text, default="")
    status       = Column(String, default="open", index=True)  # open | reviewed | dismissed
    created_at   = Column(DateTime, default=datetime.utcnow)

class Favorite(Base):
    __tablename__ = "favorites"
    id              = Column(Integer, primary_key=True, index=True)
    user_id         = Column(Integer, ForeignKey("users.id"), index=True)
    announcement_id = Column(Integer, ForeignKey("announcements.id"), index=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "announcement_id", name="uq_favorite_pair"),)

class PasswordReset(Base):
    __tablename__ = "password_resets"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), index=True)
    token_hash = Column(String, index=True)
    expires_at = Column(DateTime, index=True)
    used       = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    Base.metadata.create_all(bind=engine)
    # Migrate: add new columns if they don't exist (SQLite)
    _migrations = [
        "ALTER TABLE users ADD COLUMN passport_number VARCHAR",
        "ALTER TABLE users ADD COLUMN passport_expiry VARCHAR",
        "ALTER TABLE users ADD COLUMN is_deleted BOOLEAN DEFAULT 0",
        "ALTER TABLE users ADD COLUMN locale VARCHAR DEFAULT 'en'",
        "ALTER TABLE announcements ADD COLUMN hotel_name_madinah VARCHAR",
        "ALTER TABLE announcements ADD COLUMN hotel_stars_madinah INTEGER DEFAULT 3",
        "ALTER TABLE announcements ADD COLUMN makkah_checkin DATETIME",
        "ALTER TABLE announcements ADD COLUMN makkah_checkout DATETIME",
        "ALTER TABLE announcements ADD COLUMN madinah_checkin DATETIME",
        "ALTER TABLE announcements ADD COLUMN madinah_checkout DATETIME",
        "ALTER TABLE announcements ADD COLUMN is_deleted BOOLEAN DEFAULT 0",
        "ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0",
    ]
    with engine.connect() as conn:
        for sql in _migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass  # Column already exists
        # One-time cleanup: historical placeholder national IDs must not block
        # future registrations (uniqueness only applies to real values).
        try:
            conn.execute(text(
                "UPDATE users SET national_id='' "
                "WHERE lower(national_id) IN ('n/a','na','none','-')"
            ))
            conn.commit()
        except Exception:
            pass
