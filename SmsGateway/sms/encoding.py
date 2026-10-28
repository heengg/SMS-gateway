from dataclasses import dataclass

@dataclass
class SmsSegmentResult:
    encoding: str
    parts: int
    effective_length: int

## COunting SMS

# GSM 03.38 Basic Character Set (Standard characters)
GSM_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ "
    "!\"#¤%&'()*+,-./0123456789:;<=>?¡"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿"
    "abcdefghijklmnopqrstuvwxyzäöñüà"
)

GSM_EXTENDED = set("\f^{}\\[~]|€")

def countSmsParts(message_text):
    if not message_text:
        return SmsSegmentResult(encoding="GSM-7", parts=0, effective_length=0)
    
    is_gsm = all(char in GSM_BASIC or char in GSM_EXTENDED for char in message_text)

    length = len(message_text)
    effective_length = sum(2 if char in GSM_EXTENDED else 1 for char in message_text)

    if is_gsm:
        if effective_length <= 160:
            return SmsSegmentResult(encoding="GSM-7", parts=1, effective_length=effective_length)
        else:
            return SmsSegmentResult(encoding="GSM-7", parts=(length + 152) // 153, effective_length=effective_length)
    else:
        length = len(message_text)
        if length <= 70:
            return SmsSegmentResult(encoding="Unicode", parts=1,effective_length=length)
        else:
            return SmsSegmentResult(encoding="Unicode", parts=(length + 66) // 67, effective_length=length)





# #A-z , 0-9
# #1 SMS holds 160 characters, longer message parts of 153
# def gsm():
#     return 1

# #devnagari or emoji
# #1 SMS holds 70 characters, longer message parts of 67
# def unicode():
#     return 1