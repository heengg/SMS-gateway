import re

###Valid = 10 digits starting 97 or 98, after stripping an optional +977 or 977 prefix.

def validate(phone_number:str) -> str:
         return checkPhone(phone_number);

def checkPhone(phone_number:str) -> str:
    if not phone_number:
        raise ValueError("Phone number cannot be empty.")
    # if len(phone_number) <= 9:
    #     raise ValueError("invalid.")
    
    phone_number = re.sub(r'[^\d]','',phone_number);
    # Remove any leading +977 or 977 prefix
    # re.fullmatch(r"^(?:977)?(\d{10})$", digits_only)
    phone_number = re.fullmatch(r"(?:^\+?977)?([0-9]{10})",phone_number)
    if not phone_number:
        raise ValueError("Invalid phone number format.")
        
    core_number = phone_number.group(1)

    if not core_number.startswith(('97', '98')):
        raise ValueError(f"Invalid mobile number: '{core_number}' is not a valid 10-digit number.")

    return core_number



if __name__ == "__main__":
    test_numbers = [
        "+91 98765 43210",
        "919876543210",
        "9841234567",
        "+9779801122334",
        "9779761234567",
        "9612345678",
        "984123456",
        "+91-98765-43210",
        "09876543210",
        "9876543210",
        ]

    print(" STARTING PHONE NUMBER VALIDATION TEST")
    for num in test_numbers:
        try:
            print(f"Original: {num:18} -> Cleaned: {validate(num)}")
        except ValueError as e:
            print(f"Original: {num:18} -> Error: {e}")
    print("--- TEST COMPLETE ---")