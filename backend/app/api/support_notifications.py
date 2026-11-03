from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.models import Announcement, Notification, SupportMessage, SupportTicket, User
from app.schemas.schemas import (
    NotificationResponse,
    SupportMessageResponse,
    SupportReplyRequest,
    SupportTicketCreateRequest,
    SupportTicketResponse,
)
from app.security.auth import get_current_user

router = APIRouter(prefix="/api", tags=["Support & Notifications"])


def _build_ticket_response(db: Session, ticket: SupportTicket) -> SupportTicketResponse:
    owner = db.query(User).filter(User.id == ticket.user_id).first()
    msgs = (
        db.query(SupportMessage)
        .filter(SupportMessage.ticket_id == ticket.id)
        .order_by(SupportMessage.created_at.asc())
        .all()
    )
    return SupportTicketResponse(
        id=ticket.id,
        user_id=ticket.user_id,
        user_email=owner.email if owner else None,
        subject=ticket.subject,
        description=ticket.description,
        status=ticket.status,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        messages=[
            SupportMessageResponse(
                id=m.id,
                sender_user_id=m.sender_user_id,
                is_admin_reply=m.is_admin_reply,
                message=m.message,
                created_at=m.created_at,
            )
            for m in msgs
        ],
    )


@router.get("/support/tickets", response_model=List[SupportTicketResponse])
def list_my_tickets(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    tickets = (
        db.query(SupportTicket)
        .filter(SupportTicket.user_id == current_user.id)
        .order_by(SupportTicket.updated_at.desc())
        .all()
    )
    return [_build_ticket_response(db, t) for t in tickets]


@router.post("/support/tickets", response_model=SupportTicketResponse, status_code=status.HTTP_201_CREATED)
def create_ticket(
    payload: SupportTicketCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ticket = SupportTicket(
        user_id=current_user.id,
        subject=payload.subject.strip(),
        description=payload.description.strip(),
        status="Open",
    )
    db.add(ticket)
    db.flush()
    db.add(
        SupportMessage(
            ticket_id=ticket.id,
            sender_user_id=current_user.id,
            is_admin_reply=False,
            message=payload.description.strip(),
        )
    )
    db.commit()
    db.refresh(ticket)
    return _build_ticket_response(db, ticket)


@router.get("/support/tickets/{ticket_id}", response_model=SupportTicketResponse)
def get_ticket(
    ticket_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support ticket not found")
    if ticket.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return _build_ticket_response(db, ticket)


@router.post("/support/tickets/{ticket_id}/reply", response_model=SupportTicketResponse)
def reply_to_ticket(
    ticket_id: int,
    payload: SupportReplyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support ticket not found")
    if ticket.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if ticket.status == "Closed" and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ticket is closed")

    db.add(
        SupportMessage(
            ticket_id=ticket.id,
            sender_user_id=current_user.id,
            is_admin_reply=current_user.is_admin,
            message=payload.message.strip(),
        )
    )
    if current_user.is_admin:
        ticket.status = payload.new_status if payload.new_status in ("Open", "In Progress", "Replied", "Closed") else "Replied"
    else:
        ticket.status = "In Progress" if ticket.status == "Replied" else ticket.status

    db.commit()
    db.refresh(ticket)
    return _build_ticket_response(db, ticket)


@router.get("/notifications", response_model=List[NotificationResponse])
def list_notifications(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        NotificationResponse(
            id=n.id,
            title=n.title,
            message=n.message,
            category=n.category,
            is_read=n.is_read,
            created_at=n.created_at,
        )
        for n in items
    ]


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notif = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == current_user.id)
        .first()
    )
    if notif:
        notif.is_read = True
        db.commit()
    return {"message": "Marked as read"}


@router.post("/notifications/read-all")
def mark_all_notifications_read(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id, Notification.is_read.is_(False))
        .update({"is_read": True})
    )
    db.commit()
    return {"message": "All notifications marked read"}


@router.get("/announcements")
def list_published_announcements(db: Session = Depends(get_db)):
    items = (
        db.query(Announcement)
        .filter(Announcement.is_published.is_(True))
        .order_by(Announcement.created_at.desc())
        .all()
    )
    return [
        {
            "id": a.id,
            "title": a.title,
            "content": a.content,
            "is_published": a.is_published,
            "created_at": a.created_at,
        }
        for a in items
    ]
