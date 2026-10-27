# sms/services.py
import re
from django.db import transaction
from .models import SMSRoute, SMSLog
from .phone_validation import validate
from .encoding import countSmsParts

def dispatch_bulk_sms_pipeline(user_obj, recipient_list: list, message_text: str) -> dict:
    """
    Core Business Logic Engine.
    - Validates and normalizes phone lists.
    - Locks the user's wallet via a safe database transaction block.
    - Smart routes message text (selects transactional lanes for OTPs automatically).
    - Executes failover routing across networks (Ncell vs NTC).
    - Automatically executes precise refund logic for failed streams.
    """
    # 1. Trigger our custom validation module
    clean_phones = []
    invalid_phones = []
    
    for num in recipient_list:
        try:
            clean_phones.append(validate(num))
        except ValueError as err:
            invalid_phones.append({"input": num, "error": str(err)})

    if invalid_phones:
        return {"success": False, "error_type": "ValidationError", "details": invalid_phones}

    # 2. Analyze the character footprint metrics
    encoding_type, segments_count = countSmsParts(message_text)
    
    # 3. Smart Routing Selection: Look for critical verification intent context
    # If the message matches an OTP/Login blueprint, force transactional paths first
    is_otp = bool(re.search(r'(otp|verification|verify|code|login|password)', message_text, re.IGNORECASE))
    target_route_type = 'transactional' if is_otp else 'promotional'

    # Fetch and rank available routes
    available_routes = list(
        SMSRoute.objects.filter(is_active=True)
        .order_by('priority')
    )
    
    # Prioritize the matched route type (transactional vs promotional)
    sorted_routes = sorted(available_routes, key=lambda r: 0 if r.route_type == target_route_type else 1)

    if not sorted_routes:
        return {"success": False, "error": "No active communication gateway routes are available."}

    # 5. Open an isolated atomic database transaction block to prevent overspending
    # Satisfies the requirement: 20 parallel requests fired simultaneously at 10 credits worth of balance
    with transaction.atomic():
        # select_for_update() locks this unique row in the database until this block finishes executing
        user_wallet = user_obj.wallet.select_for_update().get()
        
        # Calculate maximum possible upfront cost assuming the top-priority route's cost footprint
        primary_route = sorted_orutes[0]
        max_possible_cost = len(clean_phones) * segments_count * primary_route.cost_per_segment
        
        if user_wallet.balance < max_possible_cost:
            raise ValueError(f"Insufficient wallet balance. Needed: {max_possible_cost}, Available: {user_wallet.balance}")

        # Deduct the cost upfront before releasing control to external network simulators
        user_wallet.balance -= max_possible_cost
        user_wallet.save()

    # 6. Dispatch Loop & Fallback Routing Chain
    successful_logs = []
    failed_logs = []
    actual_credits_spent = 0

    for phone in clean_phones:
        sms_delivered = False
        route_attempted = None
        
        # Initialize a base audit trail tracking record row in our system
        log_entry = SMSLog.objects.create(
            user=user_obj,
            recipient_phone_number=phone,
            message_body=message_text,
            encoding_type=encoding_type,
            segments_count=segments_count,
            credit_cost=0, # Will be set dynamically based on the route that actually succeeds
            status='pending'
        )

        # Fallback mechanism: Attempt routes sequentially by priority if one crashes
        for route in sorted_routes:
            route_attempted = route
            # Use select_for_update to lock route balances so parallel tasks don't overdraw provider inventory
            with transaction.atomic():
                locked_route = SMSRoute.objects.select_for_update().get(id=route.id)
                
                # Check model internal simulation engine function
                if locked_route.send_sms(phone, message_text):
                    sms_delivered = True
                    # Calculate dynamic structural cost based on this specific lane's pricing constraint
                    final_cost = segments_count * locked_route.cost_per_segment
                    actual_credits_spent += final_cost
                    
                    # Update audit log entry state
                    log_entry.route_used = locked_route
                    log_entry.credit_cost = final_cost
                    log_entry.status = 'sent'
                    log_entry.save()
                    break # Success! Break the inner route fallback loop, move to next phone number

        if not sms_delivered:
            log_entry.status = 'failed'
            log_entry.save()
            failed_logs.append(phone)
        else:
            successful_logs.append(phone)

    # 7. Refund Layer (Requirement 06)
    # If our actual spend is less than the max cost deducted upfront (or if items failed), give credits back!
    refund_amount = max_possible_cost - actual_credits_spent
    
    if refund_amount > 0:
        with transaction.atomic():
            refund_wallet = user_obj.wallet.select_for_update().get()
            refund_wallet.balance += refund_amount
            refund_wallet.save()

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
