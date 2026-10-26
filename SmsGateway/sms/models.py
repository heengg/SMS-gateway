import random  # Fix 1: Imported random simulation engine
from django.db import models
from django.contrib.auth.models import User
from django.db.models import Q, CheckConstraint 

class SMSProvider(models.Model):
    name = models.CharField(max_length=50, unique=True)
    is_active = models.BooleanField(default=True)

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

    class Meta:
        unique_together = ('provider', 'route_type')
        constraints = [
            CheckConstraint(
                check=Q(route_balance__gte=0),
                name="route_balance_cannot_be_negative"
            )
        ]
    
    def send_sms(self, to_number: str, text: str) -> bool:
        """Simulates an upstream network dispatch. Returns True on success, False on failure."""
        if self.route_balance <= 0:
            return False
            
        if random.random() < self.failure_rate:
            return False
            
        self.route_balance -= 1
        self.save()
        return True

    def getBalance(self) -> int:
        return self.route_balance

    def __str__(self):
        return f"Provider Route: {self.route_name} | (Priority {self.priority}) | Balance: {self.route_balance}"

class Wallet(models.Model):
    # Link each wallet strictly to one Django authentication User
    user = models.OneToOneField(User, on_delete=models.CASCADE)
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


class SMSLog(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    recipient_phone_number = models.CharField(max_length=15, db_index=True) # Indexing makes search filtering blazing fast
    message_body = models.TextField()
    encoding_type = models.CharField(max_length=10) # e.g., 'GSM-7' or 'Unicode'
    segments_count = models.IntegerField()
    credit_cost = models.IntegerField()
    route_used = models.ForeignKey(SMSRoute, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"To: {self.recipient_phone_number} | Status: {self.status}"
