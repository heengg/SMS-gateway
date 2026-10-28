from django.test import TestCase
from django.contrib.auth.models import User
from unittest.mock import patch, MagicMock

from sms.encoding import countSmsParts
from sms.phone_validation import validate
from sms.models import SMSProvider, SMSRoute, Wallet, SMSLog
from sms.services import dispatch_bulk_sms_pipeline


class SMSEncodingTests(TestCase):
    """Tests for the segment counter (Bonus B4)"""

    def test_gsm_7_single_segment(self):
        # 160 standard GSM characters should be exactly 1 part
        message = "A" * 160
        result = countSmsParts(message)
        self.assertEqual(result.parts, 1)
        self.assertEqual(result.encoding, "GSM-7")

    def test_gsm_7_multi_segment(self):
        # 161 standard characters should split into 2 parts (153 + 8)
        message = "A" * 161
        result = countSmsParts(message)
        self.assertEqual(result.parts, 2)

    def test_unicode_single_segment(self):
        # Emojis trigger Unicode encoding (70 chars max per segment)
        message = "👍" * 70
        result = countSmsParts(message)
        self.assertEqual(result.parts, 1)
        self.assertEqual(result.encoding, "Unicode")

    def test_unicode_multi_segment(self):
        # 71 emojis should split into 2 parts (67 + 4)
        message = "👍" * 71
        result = countSmsParts(message)
        self.assertEqual(result.parts, 2)


class PhoneValidationTests(TestCase):
    """Tests for number validation (Bonus B4)"""

    def test_valid_nepali_number_standard(self):
        # Standard 10-digit Nepali number should pass
        result = validate("9841234567")
        self.assertEqual(result, "9841234567") # Adjust if your validate function adds +977

    def test_valid_nepali_number_with_country_code(self):
        # Number with +977 should be normalized
        result = validate("+9779801122334")
        # Adjust expected output based on your exact normalize logic (e.g., "9779801122334" or "9801122334")
        self.assertIsNotNone(result) 

    def test_invalid_number_too_short(self):
        with self.assertRaises(ValueError):
            validate("123")

    def test_invalid_number_contains_letters(self):
        with self.assertRaises(ValueError):
            validate("984123ABCD")


class FailoverLogicTests(TestCase):
    """Tests for the priority-based failover logic (Bonus B4)"""

    def setUp(self):
        # Create a test user with a wallet
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.wallet = Wallet.objects.create(user=self.user, balance=100)
        
        # Create a mock provider
        self.provider = SMSProvider.objects.create(name='TEST_PROVIDER')
        
        # Create Route A (Priority 1) and Route B (Priority 2)
        self.route_a = SMSRoute.objects.create(
            provider=self.provider, route_name='ROUTE_A', priority=1, 
            route_type='promotional', route_balance=5000, failure_rate=0.0, cost_per_segment=1, is_active=True
        )
        self.route_b = SMSRoute.objects.create(
            provider=self.provider, route_name='ROUTE_B', priority=2, 
            route_type='promotional', route_balance=5000, failure_rate=0.0, cost_per_segment=1, is_active=True
        )

    @patch('sms.models.SMSRoute.send_sms')
    def test_failover_to_second_route_on_first_route_failure(self, mock_send_sms):
        """
        This is the "Senior Level" test. 
        We mock send_sms to FAIL on Route A, but SUCCEED on Route B.
        We then verify the system correctly used Route B.
        """
        # Make the mock return False (fail) the first time it's called (Route A), 
        # and True (success) the second time it's called (Route B)
        mock_send_sms.side_effect = [False, True]

        # Execute the pipeline
        result = dispatch_bulk_sms_pipeline(
            user_obj=self.user, 
            recipient_list=["9841234567"], 
            message_text="Test failover message"
        )

        # 1. Verify the pipeline reports success
        self.assertTrue(result["success"])
        self.assertEqual(result["summary"]["successfully_sent"], 1)

        # 2. Verify the SMSLog shows that ROUTE_B was ultimately used
        log = SMSLog.objects.filter(user=self.user).first()
        self.assertEqual(log.status, 'sent')
        self.assertEqual(log.route_used.route_name, 'ROUTE_B') # <--- THE PROOF!

        # 3. Verify Route A's balance was NOT deducted, but Route B's was
        self.route_a.refresh_from_db()
        self.route_b.refresh_from_db()
        self.assertEqual(self.route_a.route_balance, 5000) # Unchanged
        self.assertEqual(self.route_b.route_balance, 4999) # Deducted 1 segment