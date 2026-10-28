import uuid
import re
from django.db import transaction
from .models import SMSRoute, SMSLog, CreditLedger, SMSDeliveryAttempt, Wallet
from .phone_validation import validate
from .encoding import countSmsParts

def dispatch_bulk_sms_pipeline(user_obj, recipient_list: list, message_text: str) -> dict:
    clean_phones = []
    invalid_phones = []

    for num in recipient_list:
        try:
            clean_phones.append(validate(num))
        except ValueError as err:
            invalid_phones.append({"input": num, "error": str(err)})

    if invalid_phones:
        return {"success": False, "error_type": "ValidationError", "details": invalid_phones}
    if not clean_phones:
        return {"success": False, "error": "No valid phone numbers provided"}

    # 2. Analyze Message
    # Note: Ensure your countSmsParts returns a tuple (encoding, parts) or adjust this unpacking
    segment_result = countSmsParts(message_text)
    encoding_type = segment_result.encoding
    segments_count = segment_result.parts
    
    # 3. Get Routes
    available_routes = list(SMSRoute.objects.filter(is_active=True).order_by('priority'))
    
    if not available_routes:
        return {"success": False, "error": "No active routes available."}

    # 4. Calculate Cost Safely
    # FIX: Use the MOST EXPENSIVE route for the upfront hold to prevent negative balances on fallback
    max_cost_per_segment = max(route.cost_per_segment for route in available_routes)
    max_possible_cost = len(clean_phones) * segments_count * max_cost_per_segment

    # Generate a single ID for this entire bulk request (for idempotency & ledger)
    request_id = uuid.uuid4()

    # 5. Atomic Wallet Deduction & Ledger Creation
    with transaction.atomic():
        user_wallet = Wallet.objects.select_for_update().get(user=user_obj)
        # user_wallet = user_obj.wallet.select_for_update().get()
        
        if user_wallet.balance < max_possible_cost:
            raise ValueError(f"Insufficient balance. Needed: {max_possible_cost}, Available: {user_wallet.balance}")

        user_wallet.balance -= max_possible_cost
        user_wallet.save(update_fields=['balance', 'updated_at'])

        # Create the immutable audit trail
        CreditLedger.objects.create(
            user=user_obj, wallet=user_wallet, request_id=request_id,
            amount=-max_possible_cost, entry_type="DEBIT",
            balance_after=user_wallet.balance,
            description=f"Bulk SMS debit for {len(clean_phones)} recipients"
        )

        # Create pending logs for all recipients
        for phone in clean_phones:
            SMSLog.objects.create(
                request_id=request_id, user=user_obj, recipient_phone_number=phone,
                message_body=message_text, encoding_type=encoding_type,
                segments_count=segments_count, credit_cost=0, status='pending'
            )

    # 6. Lock Routes Safely (Prevents Deadlocks under parallel load)
    # We lock all relevant routes at once, ordered by ID, to guarantee consistent lock acquisition
    route_ids = [r.id for r in available_routes]
    with transaction.atomic():
        # Lock the routes so parallel requests don't overdraft provider balances
        locked_routes_map = {
            r.id: r for r in SMSRoute.objects.filter(id__in=route_ids).select_for_update().order_by('id')
        }

        successful_logs = []
        failed_logs = []
        actual_credits_spent = 0

        logs_to_process = SMSLog.objects.filter(request_id=request_id, status='pending')
    # locked_routes_map = {
    #     r.id: r for r in SMSRoute.objects.filter(id__in=route_ids).select_for_update().order_by('id')
    # }

    # Fetch the logs we just created for this request
        # logs_to_process = SMSLog.objects.filter(request_id=request_id, status='pending')

        for log_entry in logs_to_process:
            sms_delivered = False
            
            for route in available_routes:
                locked_route = locked_routes_map[route.id]
                
                # Record the attempt (Success OR Failure) for the Health Endpoint
                attempt_success = locked_route.send_sms(log_entry.recipient_phone_number, message_text, segments=segments_count)
                
                SMSDeliveryAttempt.objects.create(
                    log=log_entry, route=locked_route, 
                    success=attempt_success, 
                    error_message="" if attempt_success else "Simulated provider failure"
                )

                if attempt_success:
                    # Decrement provider balance safely
                    locked_route.route_balance -= segments_count
                    locked_route.save(update_fields=['route_balance'])
                    
                    # Update Log
                    final_cost = segments_count * locked_route.cost_per_segment
                    log_entry.route_used = locked_route
                    log_entry.credit_cost = final_cost
                    log_entry.status = 'sent'
                    log_entry.save(update_fields=['route_used', 'credit_cost', 'status'])
                    
                    actual_credits_spent += final_cost
                    successful_logs.append(log_entry.recipient_phone_number)
                    sms_delivered = True
                    break # Move to next phone

            if not sms_delivered:
                log_entry.status = 'failed'
                log_entry.save(update_fields=['status'])
                failed_logs.append(log_entry.recipient_phone_number)

    # 8. Refund Layer
    refund_amount = max_possible_cost - actual_credits_spent
    
    if refund_amount > 0:
        with transaction.atomic():
            # refund_wallet = user_obj.wallet.select_for_update().get()
            refund_wallet = Wallet.objects.select_for_update().get(user=user_obj)
            refund_wallet.balance += refund_amount
            refund_wallet.save(update_fields=['balance', 'updated_at'])
            
            CreditLedger.objects.create(
                user=user_obj, wallet=refund_wallet, request_id=request_id,
                amount=refund_amount, entry_type="REFUND",
                balance_after=refund_wallet.balance,
                description=f"Refund for {len(failed_logs)} failed messages + overcharge adjustment"
            )

    return {
        "success": True,
        "summary": {
            "total_submitted": len(clean_phones),
            "successfully_sent": len(successful_logs),
            "failed_delivery": len(failed_logs),
            "initial_cost_held": max_possible_cost,
            "actual_credits_deducted": actual_credits_spent,
            "credits_refunded": refund_amount
        }
    }