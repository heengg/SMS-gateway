# sms/views.py
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from django.core.paginator import Paginator

from .services import dispatch_bulk_sms_pipeline
from .models import SMSLog, SMSRoute

@csrf_exempt
def send_sms_view(request):
    """
    POST /sms/send
    Parses JSON payload, authenticates a mock user, and executes the sending transaction.
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": f"Method {request.method} Not Allowed"}, status=405)

    try:
        data = json.loads(request.body)
        recipients = data.get("to")
        message_text = data.get("message")

        if not recipients or not message_text:
            return JsonResponse({"success": False, "error": "Missing 'to' or 'message' parameters."}, status=400)

        # Mock Authentication Check (Using the first seeded user for simplification)
        # In production, replace this by extracting user information from your JWT token payload
        test_user = User.objects.first()
        if not test_user:
            return JsonResponse({"success": False, "error": "No database users found to authenticate request."}, status=401)

        # Dispatch execution pipeline to the service core layer
        result = dispatch_bulk_sms_pipeline(test_user, recipients, message_text)

        if not result.get("success", True):
            return JsonResponse(result, status=400)

        return JsonResponse({"success": True, "data": result}, status=201)

    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Malformed JSON request body payload."}, status=400)
    except ValueError as val_err:
        return JsonResponse({"success": False, "error": str(val_err)}, status=400)
    except Exception as e:
        return JsonResponse({"success": False, "error": "Internal Server Error"}, status=500)


def sms_logs_view(request):
    """
    GET /sms/logs
    Returns paginated message logs filterable by status, date, and recipient phone.
    """
    if request.method != "GET":
        return JsonResponse({"success": False, "error": "Method Not Allowed"}, status=405)

    # Extract clean filtering criteria out of request query parameters
    status_filter = request.GET.get('status')
    date_filter = request.GET.get('date') # Expected format: YYYY-MM-DD
    number_filter = request.GET.get('number')
    page_number = request.GET.get('page', 1)

    # Initialize basic base query
    queryset = SMSLog.objects.all().order_by('-created_at')

    # Apply database structural lookup filters dynamically based on presence
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if number_filter:
        queryset = queryset.filter(recipient_phone_number__contains=number_filter)
    if date_filter:
        queryset = queryset.filter(created_at__date=date_filter)

    paginator = Paginator(queryset, 10)
    page_obj = paginator.get_page(page_number)

    logs_data = []
    for log in page_obj:
        logs_data.append({
            "id": log.id,
            "recipient": log.recipient_phone_number,
            "message": log.message_body,
            "encoding": log.encoding_type,
            "segments": log.segments_count,
            "cost": log.credit_cost,
            "route": log.route_used.route_name if log.route_used else "None",
            "status": log.status,
            "timestamp": log.created_at.isoformat()
        })

    return JsonResponse({
        "success": True,
        "page": page_obj.number,
        "total_pages": paginator.num_pages,
        "total_records": paginator.count,
        "logs": logs_data
    }, status=200)


def routes_health_view(request):
    """
    GET /routes/health
    Computes mathematical operational health scores for routing lines over the last 24 hours.
    """
    if request.method != "GET":
        return JsonResponse({"success": False, "error": "Method Not Allowed"}, status=405)

    time_window = timezone.now() - timedelta(hours=24)
    routes = SMSRoute.objects.filter(is_active=True)
    health_report = []

    for route in routes:
        # Scan log databases specifically for this route identity footprint inside our window
        attempts = SMSLog.objects.filter(route_used=route, created_at__gte=time_window)
        total_attempts = attempts.count()
        successful_attempts = attempts.filter(status="sent").count()

        # Health = (Success / Attempts) * 100. Defaults gracefully to 100.0% if idle.
        if total_attempts > 0:
            health_percentage = round((successful_attempts / total_attempts) * 100, 2)
        else:
            health_percentage = 100.00

        health_report.append({
            "route_name": route.route_name,
            "type": route.route_type,
            "priority": route.priority,
            "provider_balance": route.getBalance(),
            "failure_rate_config": f"{route.failure_rate * 100}%",
            "health_score_24h": f"{health_percentage}%"
        })

    return JsonResponse({"success": True, "routes": health_report}, status=200)
