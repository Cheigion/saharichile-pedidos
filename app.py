import os
import json
import threading
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import gspread
from google.oauth2.service_account import Credentials

# ─── CONFIGURACION DESDE VARIABLES DE ENTORNO ────────────────────────────────
TWILIO_SID        = os.environ.get("TWILIO_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP   = os.environ.get("TWILIO_WHATSAPP", "whatsapp:+14155238886")
GOOGLE_SHEET_ID   = os.environ.get("GOOGLE_SHEET_ID", "1BI3f5VzHkBLJg1rAFmmrIhrqXVjM9MfR2vhcyCpVO9I")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "{}")
SECRET_TOKEN      = "SECRETO_123"

app = Flask(__name__)
CORS(app)  # ← Esto soluciona el CORS para todas las rutas

twilio_client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)

# ─── GOOGLE SHEETS ───────────────────────────────────────────────────────────
def get_sheet():
    try:
        creds_data = json.loads(GOOGLE_CREDS_JSON)
        scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_data, scopes=scopes)
        gc = gspread.authorize(creds)
        return gc.open_by_key(GOOGLE_SHEET_ID).sheet1
    except Exception as e:
        print(f"❌ Error conectando Google Sheets: {e}")
        return None

def guardar_pedido(nombre, email, telefono, producto):
    sheet = get_sheet()
    if not sheet:
        return None
    fila = [
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        nombre, email, telefono, producto, "PENDIENTE"
    ]
    sheet.append_row(fila)
    return len(sheet.get_all_values())

def actualizar_estado(fila, estado):
    sheet = get_sheet()
    if not sheet:
        return
    sheet.update_cell(fila, 6, estado)

def buscar_fila_por_telefono(telefono):
    sheet = get_sheet()
    if not sheet:
        return None, None
    telefono_limpio = telefono.replace("whatsapp:", "").replace("+", "").strip()
    numeros = sheet.col_values(4)
    for i, num in enumerate(numeros):
        num_limpio = num.replace("+", "").replace(" ", "").strip()
        if telefono_limpio in num_limpio or num_limpio in telefono_limpio:
            fila_num = i + 1
            fila_data = sheet.row_values(fila_num)
            return fila_num, fila_data
    return None, None

# ─── TWILIO ──────────────────────────────────────────────────────────────────
def enviar_whatsapp(numero, mensaje):
    try:
        twilio_client.messages.create(
            from_=TWILIO_WHATSAPP,
            to=f"whatsapp:{numero}",
            body=mensaje
        )
        print(f"✅ WhatsApp enviado a {numero}")
    except Exception as e:
        print(f"❌ Error enviando WhatsApp: {e}")

def enviar_reconfirmacion(numero, nombre, producto, fila):
    import time
    time.sleep(300)
    sheet = get_sheet()
    if sheet:
        estado = sheet.cell(fila, 6).value
        if estado != "PENDIENTE":
            return
    mensaje = (
        f"⏰ Hola *{nombre}*, hemos guardado tu pedido ya que es de alta demanda "
        f"y no queremos que te quedes sin el tuyo.\n\n"
        f"Deseas confirmar el envio de tu pedido de *{producto}*?\n\n"
        f"Responde *SI* para confirmar o *NO* para cancelar 🙏"
    )
    enviar_whatsapp(numero, mensaje)

# ─── WEBHOOK FORMULARIO ──────────────────────────────────────────────────────
@app.route("/pedido", methods=["POST"])
def recibir_pedido():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Sin datos"}), 400
    if data.get("token") != SECRET_TOKEN:
        return jsonify({"error": "No autorizado"}), 403

    nombre   = data.get("nombre", "Cliente")
    email    = data.get("email", "")
    telefono = data.get("telefono", "")
    producto = data.get("producto", "")

    if not telefono:
        return jsonify({"error": "Telefono requerido"}), 400

    # Formatear número chileno
    telefono_limpio = telefono.replace(" ", "").replace("-", "")
    if telefono_limpio.startswith("0"):
        telefono_limpio = "+56" + telefono_limpio[1:]
    elif telefono_limpio.startswith("9") and len(telefono_limpio) == 9:
        telefono_limpio = "+56" + telefono_limpio
    elif not telefono_limpio.startswith("+"):
        telefono_limpio = "+56" + telefono_limpio

    fila = guardar_pedido(nombre, email, telefono_limpio, producto)

    mensaje = (
        f"🛍️ Hola *{nombre}*! Gracias por tu pedido en *SahariChile* 🍱\n\n"
        f"📦 *Producto:* {producto}\n"
        f"📧 *Correo:* {email}\n"
        f"📱 *Telefono:* {telefono_limpio}\n\n"
        f"Deseas confirmar tu pedido?\n\n"
        f"Responde *SI* para confirmar ✅\n"
        f"Responde *NO* para cancelar ❌"
    )
    enviar_whatsapp(telefono_limpio, mensaje)

    if fila:
        hilo = threading.Thread(
            target=enviar_reconfirmacion,
            args=(telefono_limpio, nombre, producto, fila)
        )
        hilo.daemon = True
        hilo.start()

    return jsonify({"ok": True}), 200

# ─── WEBHOOK RESPUESTA CLIENTE ───────────────────────────────────────────────
@app.route("/respuesta", methods=["POST"])
def recibir_respuesta():
    numero  = request.form.get("From", "")
    mensaje = request.form.get("Body", "").strip().upper()

    fila, fila_data = buscar_fila_por_telefono(numero)
    resp = MessagingResponse()

    if not fila:
        resp.message("🤖 No encontramos un pedido con tu numero. Visita sahari-web.vercel.app para hacer un pedido 🍱")
        return str(resp)

    nombre   = fila_data[1] if len(fila_data) > 1 else "Cliente"
    producto = fila_data[4] if len(fila_data) > 4 else "tu producto"
    estado   = fila_data[5] if len(fila_data) > 5 else "PENDIENTE"

    if estado != "PENDIENTE":
        resp.message(f"Tu pedido ya fue procesado con estado: *{estado}* ✅")
        return str(resp)

    if mensaje in ["SI", "SÍ", "S", "YES", "OK", "CONFIRMO", "CONFIRMAR"]:
        actualizar_estado(fila, "CONFIRMADO")
        resp.message(
            f"Muchas gracias *{nombre}*! Tu pedido de *{producto}* ha sido confirmado ✅\n\n"
            f"Te enviaremos el numero de seguimiento de Starken en cuanto lo tengamos 📦🚚\n\n"
            f"Gracias por elegir *SahariChile*! 🍱"
        )
    elif mensaje in ["NO", "N", "CANCELAR", "CANCELADO"]:
        actualizar_estado(fila, "CANCELADO")
        resp.message(
            f"Gracias por avisarnos *{nombre}*. Tu pedido de *{producto}* ha sido cancelado ❌\n\n"
            f"Si cambias de opinion puedes hacer un nuevo pedido en sahari-web.vercel.app 🍱"
        )
    else:
        resp.message(
            f"Hola *{nombre}*, no entendi tu respuesta.\n\n"
            f"Por favor responde:\n"
            f"*SI* para confirmar tu pedido ✅\n"
            f"*NO* para cancelarlo ❌"
        )

    return str(resp)

# ─── HEALTH CHECK ────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "SahariChile Pedidos corriendo"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
