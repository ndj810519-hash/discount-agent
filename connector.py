from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

import os
import json
import requests
import firebase_admin

from firebase_admin import credentials, firestore
from datetime import datetime, timedelta


app = FastAPI()

# ================= CORS =================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= ENV =================
FORTE_API_URL = os.getenv("FORTE_API_URL")
FORTE_USERNAME = os.getenv("FORTE_USERNAME")
FORTE_PASSWORD = os.getenv("FORTE_PASSWORD")

# ================= FIREBASE =================
if not firebase_admin._apps:
    firebase_json = os.getenv("FIREBASE_KEY_JSON")
    cred = credentials.Certificate(json.loads(firebase_json))
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ================= CREATE ORDER =================
@app.get("/create-forte-order")
async def create_forte_order(uid: str):

    payload = {
        "order": {
            "typeRid": "Order_RID",
            "language": "ru",
            "amount": "100.00",
            "currency": "KZT",
            "description": f"{uid}|30days",
            "title": "30-day subscription",
            "hppRedirectUrl": "https://discount-backend-edey.onrender.com/forte-success"
        }
    }

    response = requests.post(
        f"{FORTE_API_URL}/order",
        json=payload,
        auth=(FORTE_USERNAME, FORTE_PASSWORD),
        headers={"Content-Type": "application/json"}
    )

    response.raise_for_status()

    forte_response = response.json()

    order_id = str(forte_response["order"]["id"])
    password = forte_response["order"]["password"]
    hpp_url = forte_response["order"]["hppUrl"]

    db.collection("forte_orders").document(order_id).set({
        "uid": uid,
        "createdAt": datetime.utcnow(),
        "isProcessed": False
    })

    return RedirectResponse(f"{hpp_url}?id={order_id}&password={password}")

# ================= SUCCESS =================
@app.get("/forte-success")
async def forte_success(request: Request):

    order_id = request.query_params.get("ID") or request.query_params.get("id")

    if not order_id:
        return RedirectResponse("http://enoma.kz/dis-auth")

    response = requests.get(
        f"{FORTE_API_URL}/order/{order_id}",
        auth=(FORTE_USERNAME, FORTE_PASSWORD)
    )

    result = response.json()
    status = result.get("order", {}).get("status")

    if status not in ["FullyPaid", "Approved", "Deposited"]:
        return RedirectResponse("http://enoma.kz/dis-auth")

    order_ref = db.collection("forte_orders").document(order_id)
    order_doc = order_ref.get()

    if not order_doc.exists:
        return RedirectResponse("http://enoma.kz/dis-auth")

    order_data = order_doc.to_dict()

    uid = order_data["uid"]
    now = datetime.utcnow()

    user_ref = db.collection("users").document(uid)
    user_doc = user_ref.get()

    # ================= ПРОДЛЕНИЕ =================
    if user_doc.exists:
        data = user_doc.to_dict()
        current_expiry = data.get("expiresAt")

        if current_expiry and current_expiry > now:
            expires_at = current_expiry + timedelta(days=30)
        else:
            expires_at = now + timedelta(days=30)
    else:
        expires_at = now + timedelta(days=30)

    # ================= ЗАЩИТА ОТ ДУБЛЕЙ =================
    if not order_data.get("isProcessed"):
        user_ref.set({
            "hasAccess": True,
            "expiresAt": expires_at,
            "lastPaymentAt": now
        }, merge=True)

        order_ref.update({
            "isProcessed": True,
            "paidAt": now
        })

    return RedirectResponse(f"http://enoma.kz/discount-astana?uid={uid}&paid=1")

# ================= STATUS =================
@app.get("/subscription-status")
def subscription_status(uid: str):

    user_ref = db.collection("users").document(uid)
    user_doc = user_ref.get()

    # ❌ нет пользователя
    if not user_doc.exists:
        return RedirectResponse("http://enoma.kz/dis-auth")

    data = user_doc.to_dict()
    expires_at = data.get("expiresAt")

    # ❌ нет подписки
    if not expires_at:
        return RedirectResponse("http://enoma.kz/dis-auth")

    if hasattr(expires_at, "tzinfo") and expires_at.tzinfo:
        expires_at = expires_at.replace(tzinfo=None)

    now = datetime.utcnow()
    remaining = int((expires_at - now).total_seconds())

    # ❌ подписка закончилась
    if remaining <= 0:
        return RedirectResponse("http://enoma.kz/dis-auth")

    return {
        "hasAccess": True,
        "remainingSeconds": remaining
    }

# ================= STATIC =================
@app.get("/manifest.json")
def manifest():
    return FileResponse("manifest.json")

@app.get("/icon-192.png")
def icon_192():
    return FileResponse("icon-192.png")

@app.get("/icon-512.png")
def icon_512():
    return FileResponse("icon-512.png")
