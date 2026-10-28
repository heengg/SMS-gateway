import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from django.core.paginator import Paginator

# DRF Imports for JWT
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .services import dispatch_bulk_sms_pipeline
from .models import SMSLog, SMSRoute, SMSDeliveryAttempt, Wallet

@api_view(['POST'])
@permission_classes([]) # Anyone can register
def register_view(request):
    username = request.data.get('username')
    password = request.data.get('password')
    if not username or not password:
        return Response({"error": "Username and password required"}, status=400)
    
    if User.objects.filter(username=username).exists():
        return Response({"error": "User already exists"}, status=400)

    user = User.objects.create_user(username=username, password=password)
    Wallet.objects.create(user=user, balance=100) # Give them 100 starter credits
    return Response({"success": True, "message": "User registered. Wallet created with 100 credits."}, status=201)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def send_sms_view(request):
    """POST /api/sms/send"""
    recipients = request.data.get("to")
    message_text = request.data.get("message")

    if not recipients or not message_text:
        return Response({"success": False, "error": "Missing 'to' or 'message'."}, status=400)

    try:
        result = dispatch_bulk_sms_pipeline(request.user, recipients, message_text)
        if not result.get("success", True):
            return Response(result, status=400)
        return Response({"success": True, "data": result}, status=201)
    except ValueError as val_err:
        return Response({"success": False, "error": str(val_err)}, status=400)
    except Exception as e:
        import traceback
        print(traceback.format_exc()) # This prints the full error to your terminal
        return Response({"success": False, "error": str(e)}, status=500) # This shows it in Postman

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sms_logs_view(request):
    """GET /api/sms/logs"""
    # FIX: Only show logs for the authenticated user
    queryset = SMSLog.objects.filter(user=request.user).order_by('-created_at')

    status_filter = request.query_params.get('status')
    date_filter = request.query_params.get('date')
    number_filter = request.query_params.get('number')

    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if number_filter:
        queryset = queryset.filter(recipient_phone_number__contains=number_filter)
    if date_filter:
        queryset = queryset.filter(created_at__date=date_filter)

    paginator = Paginator(queryset, 10)
    page_obj = paginator.get_page(request.query_params.get('page', 1))

    logs_data = [{
        "id": log.id, "recipient": log.recipient_phone_number,
        "message": log.message_body, "encoding": log.encoding_type,
        "segments": log.segments_count, "cost": log.credit_cost,
        "route": log.route_used.route_name if log.route_used else None,
        "status": log.status, "timestamp": log.created_at.isoformat()
    } for log in page_obj]

    return Response({
        "success": True, "page": page_obj.number,
        "total_pages": paginator.num_pages, "logs": logs_data
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def routes_health_view(request):
    """GET /api/routes/health"""
    time_window = timezone.now() - timedelta(hours=24)
    routes = SMSRoute.objects.filter(is_active=True)
    health_report = []

    for route in routes:
        # FIX: Use SMSDeliveryAttempt to get TRUE success rate (including failures)
        attempts = SMSDeliveryAttempt.objects.filter(route=route, attempted_at__gte=time_window)
        total = attempts.count()
        successes = attempts.filter(success=True).count()

        success_rate = (successes / total * 100) if total > 0 else 0
        
        # HEALTH FORMULA (Document this in your README!):
        # Health = (Success Rate * 0.7) + (Balance Health * 0.3)
        # Balance Health assumes 5000 is the max starting balance.
        balance_health = min((route.route_balance / 5000) * 100, 100)
        health_score = (success_rate * 0.7) + (balance_health * 0.3)

        health_report.append({
            "route_name": route.route_name,
            "provider_balance": route.getBalance(),
            "success_rate_24h": f"{success_rate:.2f}%",
            "health_score": f"{health_score:.2f}%"
        })

    return Response({"success": True, "routes": health_report})