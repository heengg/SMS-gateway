import random
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q, CheckConstraint, UniqueConstraint, Index

# from django.contrib.auth.models import User

class SMSProvider(models.Model):
    name = models.CharField(max_length=50, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
class SMSRoute(models.Model):
    ROUTE_TYPE_CHOICES = [
        ('transactional', 'Transactional (OTP, 0% failure)'),
        ('promotional', 'Promotional (Bulk, cheap, high failure)'),
    ]

    provider = models.ForeignKey(SMSProvider, on_delete=models.CASCADE, related_name="routes")
    route_name = models.CharField(max_length=100) # e.g., 'Ncell Promotional'
    route_type = models.CharField(max_length=20, choices=ROUTE_TYPE_CHOICES, default='promotional')
    priority = models.IntegerField(default=1) # 1 = Highest priority
    route_balance = models.IntegerField(default=5000) # Requirement 02: Route balance
    failure_rate = models.FloatField(default=0.30)
    cost_per_segment = models.IntegerField(default=1) # Transactional can cost more credits than promo
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('provider', 'route_type')
        constraints = [
            CheckConstraint(
                check=Q(route_balance__gte=0),
                name="route_balance_cannot_be_negative"
            ),
            CheckConstraint(
                check=Q(failure_rate__gte=0.0) & Q(failure_rate__lte=1.0),
                name="failure_rate_between_0_and_1",
            ),
            CheckConstraint(
                check=Q(cost_per_segment__gte=1),
                name="cost_per_segment_must_be_positive",
            ),
            CheckConstraint(
                check=Q(priority__gte=1),
                name="route_priority_must_be_positive",
            ),
        ]
    
    def send_sms(self, to_number: str, text: str,segments: int = 1) -> bool:
        """Simulates an upstream network dispatch. Returns True on success, False on failure."""
        if not self.is_active or self.route_balance < segments:
            return False
        return random.random() >= self.failure_rate

    def getBalance(self) -> int:
        return self.route_balance

    def __str__(self):
        return f"Provider Route: {self.route_name} | (Priority {self.priority}) | Balance: {self.route_balance}"

class Wallet(models.Model):
    # Link each wallet strictly to one Django authentication User
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,related_name="wallet")
    balance = models.IntegerField(default=100)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            CheckConstraint(
                check=Q(balance__gte=0), 
                name="wallet_balance_cannot_be_negative"
            )
        ]
    def __str__(self):
        return f"{self.user.username}'s Wallet - Balance: {self.balance}"

class CreditLedger(models.Model):
    ENTRY_TYPES = [
        ("DEBIT", "Debit"),
        ("REFUND", "Refund"),
        ("TOPUP", "Top-up"),
        ("ADJUSTMENT", "Adjustment"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ledger"
    )
    wallet = models.ForeignKey(
        Wallet, on_delete=models.PROTECT, related_name="entries"
    )
    request_id = models.UUIDField(null=True, blank=True, db_index=True)

    amount = models.IntegerField(
        help_text="Signed: -20 for a debit, +20 for a refund."
    )
    entry_type = models.CharField(max_length=12, choices=ENTRY_TYPES)
    balance_after = models.IntegerField(
        help_text="Snapshot for reconciliation / audit."
    )
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            Index(fields=["user", "-created_at"], name="ledger_user_time_idx"),
        ]
        constraints = [
            # Exactly ONE debit per send request -> a retried request
            # cannot charge twice.
            UniqueConstraint(
                fields=["request_id"],
                condition=Q(entry_type="DEBIT"),
                name="one_debit_per_send_request",
            ),
        ]

    def __str__(self):
        return f"{self.entry_type} {self.amount:+d} → {self.balance_after}"

class SMSLog(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]

    request_id = models.UUIDField(default=uuid.uuid4, db_index=True)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,related_name="sms_logs")
    recipient_phone_number = models.CharField(max_length=15, db_index=True) # Indexing makes search filtering blazing fast
    message_body = models.TextField()
    encoding_type = models.CharField(max_length=10) # e.g., 'GSM-7' or 'Unicode'
    segments_count = models.IntegerField()
    credit_cost = models.IntegerField()
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="pending", db_index=True
    )
    route_used = models.ForeignKey(SMSRoute, on_delete=models.SET_NULL, null=True, blank=True,related_name="logs")

    refunded_at = models.DateTimeField(null=True, blank=True)
    error_message = models.CharField(max_length=255, blank=True)
    
    # status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            Index(
                fields=["user", "status", "-created_at"],
                name="smslog_user_status_time_idx",
            ),
            Index(fields=["request_id", "recipient_phone_number"],
                  name="smslog_request_recipient_idx"),
        ]
        constraints = [
            # Retrying the same batch must not create duplicate messages.
            UniqueConstraint(
                fields=["request_id", "recipient_phone_number"],
                name="unique_recipient_per_request",
            ),
            CheckConstraint(
                check=Q(segments_count__gte=1),
                name="segments_must_be_positive",
            ),
        ]

    @property
    def is_refunded(self) -> bool:
        return self.refunded_at is not None

    def __str__(self):
        return f"To: {self.recipient_phone_number} | Status: {self.status}"


class SMSDeliveryAttempt(models.Model):
    """
    One row per (message, route) attempt — SUCCESS OR FAILURE.

    Without this table you can only count successes, so your
    'success rate over last 24h' would always be 100%.
    """
    log = models.ForeignKey(
        SMSLog, on_delete=models.CASCADE, related_name="attempts"
    )
    route = models.ForeignKey(
        SMSRoute, on_delete=models.PROTECT, related_name="attempts"
    )
    success = models.BooleanField(default=False)
    error_message = models.CharField(max_length=255, blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            Index(fields=["route", "-attempted_at"], name="attempt_route_time_idx"),
        ]
        constraints = [
            UniqueConstraint(
                fields=["log", "route"],
                name="one_attempt_per_route_per_message",
            ),
        ]

    def __str__(self):
        return f"{self.route.route_name} → {'OK' if self.success else 'FAIL'}"