import logging
import time
from datetime import timedelta

from app.config import settings
from app.database.session import SessionLocal
from app.hosting_runtime.isolation import build_isolated_container_spec
from app.models.models import HostingInstance, Notification, VpsServer, utcnow
from app.vps_manager.client import VpsManagementClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("smarthost.worker")


def run_monitoring_cycle() -> None:
    db = SessionLocal()
    try:
        client = VpsManagementClient(db)
        servers = db.query(VpsServer).filter(VpsServer.is_enabled.is_(True)).all()
        for vps in servers:
            if vps.connection_status == "Offline":
                continue
            ok, msg = client.verify_and_collect_health(vps)
            logger.info("VPS health check [%s]: ok=%s status=%s", vps.name, ok, msg)

        # Enforce hosting expiry & 48h warning notifications
        now = utcnow()
        warn_cutoff = now + timedelta(hours=48)
        active_instances = db.query(HostingInstance).filter(HostingInstance.status != "Expired").all()
        for inst in active_instances:
            if inst.expires_at <= now:
                if inst.assigned_vps_id:
                    vps = db.query(VpsServer).filter(VpsServer.id == inst.assigned_vps_id).first()
                    if vps and vps.connection_status == "Online":
                        try:
                            spec = build_isolated_container_spec(
                                user_id=inst.user_id,
                                hosting_id=inst.id,
                                hosting_type=inst.hosting_type,
                                cpu_limit_cores=inst.cpu_limit_cores,
                                ram_limit_mb=inst.ram_limit_mb,
                                storage_limit_mb=inst.storage_limit_mb,
                                entry_file=inst.entry_file,
                                subdomain_slug=inst.subdomain_slug,
                                env_vars={},
                            )
                            client.sync_workspace_and_control_container(vps, "stop", spec, None)
                        except Exception:
                            pass
                inst.status = "Expired"
                db.add(
                    Notification(
                        user_id=inst.user_id,
                        title="Hosting Expired",
                        message=f"'{inst.name}' has reached its expiry date and been stopped. Renew your plan to resume.",
                        category="Hosting Expiry Warning",
                    )
                )
            elif inst.expires_at <= warn_cutoff:
                existing_warn = (
                    db.query(Notification)
                    .filter(
                        Notification.user_id == inst.user_id,
                        Notification.category == "Hosting Expiry Warning",
                        Notification.message.like(f"%'{inst.name}' expires soon%"),
                    )
                    .first()
                )
                if not existing_warn:
                    db.add(
                        Notification(
                            user_id=inst.user_id,
                            title="Hosting Expiry Warning",
                            message=f"'{inst.name}' expires soon ({inst.expires_at.strftime('%Y-%m-%d %H:%M UTC')}).",
                            category="Hosting Expiry Warning",
                        )
                    )
        db.commit()
    except Exception as exc:
        logger.error("Worker cycle failed: %s", exc)
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    logger.info("SmartHost Background Worker started (interval=%ss)", settings.WORKER_POLL_INTERVAL_SECONDS)
    while True:
        run_monitoring_cycle()
        time.sleep(max(10, settings.WORKER_POLL_INTERVAL_SECONDS))
