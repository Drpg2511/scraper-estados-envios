"""
Portal de clientes de "Meridiano Envíos", una empresa de transporte ficticia.

Imita la estructura del portal real contra el que se escribió el scraper:
login con casilla de verificación, menú hasta "Consulta de envíos", buscador
por número de envío y ficha con estado, fechas e incidencias. También copia
dos detalles de comportamiento que el scraper tiene que tolerar:
  - el botón Buscar envía el formulario unos milisegundos DESPUÉS del clic;
  - la casilla Estado se pinta vacía y se rellena un momento después.

Y puede fallar a propósito, para demostrar reintentos y reanudación:
  --lento-prob P --lento-seg S   una fracción P de las búsquedas tarda S segundos
  --error-cada N                 cada N búsquedas, una responde error 500
  --caducar-tras N               la sesión caduca tras N búsquedas
  --verificacion-manual          la casilla de verificación no habilita "Acceder"

Uso:
    python portal_demo/servidor.py --puerto 8765 [opciones de fallo]
Solo usa la biblioteca estándar.
"""
import argparse
import html
import json
import random
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

try:
    from portal_demo import datos
except ImportError:  # ejecutado como script: python portal_demo/servidor.py
    import datos

ESTILOS = """
body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:0;background:#f4f5f7;color:#1d2530}
header{background:#123a5a;color:#fff;padding:12px 24px;display:flex;align-items:center;gap:24px}
header b{font-size:18px} main{max-width:760px;margin:32px auto;background:#fff;padding:24px 32px;border-radius:6px}
label{display:block;margin:12px 0 4px;font-size:14px;color:#4a5563}
input[type=text],input[type=password]{width:100%;padding:8px;border:1px solid #c7ccd4;border-radius:4px;box-sizing:border-box}
.boton,button{display:inline-block;margin-top:16px;padding:8px 18px;background:#1f6fb2;color:#fff;border:0;border-radius:4px;text-decoration:none;cursor:pointer}
button:disabled{background:#9aa5b1;cursor:not-allowed}
table{border-collapse:collapse;width:100%;margin-top:8px} td,th{border-bottom:1px solid #e3e6ea;padding:6px;text-align:left;font-size:14px}
.aviso{margin-top:16px;padding:10px;background:#fff4e5;border:1px solid #f0c27b;border-radius:4px}
nav svg{cursor:pointer;vertical-align:middle} nav .sub{background:#0f2f49;padding:8px 16px;margin-top:8px}
nav a,nav span{color:#fff;cursor:pointer;text-decoration:none;display:block;padding:4px 0}
"""


def pagina(titulo, cuerpo, menu=False):
    menu_html = ""
    if menu:
        menu_html = """
<nav>
  <svg id="menu-envios" width="28" height="28" viewBox="0 0 24 24"><path fill="#fff" d="M3 6h13v9H3zM16 9h3l2 3v3h-5z"/></svg>
  <div id="submenu-envios" class="sub" hidden>
    <div id="menu-gestion"><span>Gestión de envío</span></div>
    <ul id="submenu-gestion" hidden>
      <li><a href="/inicio">Solicitar envío</a></li>
      <li><a href="/inicio">Solicitar recogida</a></li>
      <li><a id="menu-consulta" href="/envios">Consulta de envíos</a></li>
    </ul>
  </div>
</nav>
<script>
document.getElementById('menu-envios').addEventListener('click', function () {
  var s = document.getElementById('submenu-envios'); s.hidden = !s.hidden;
});
document.querySelector('#menu-gestion > span').addEventListener('click', function () {
  var s = document.getElementById('submenu-gestion'); s.hidden = !s.hidden;
});
</script>"""
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>{titulo} - Meridiano Envíos (demo)</title><style>{ESTILOS}</style></head>
<body><header><b>Meridiano Envíos</b><small>portal de clientes · demostración</small>{menu_html}</header>
<main>{cuerpo}</main></body></html>"""


class EstadoPortal:
    """Datos, sesiones y contadores compartidos entre los hilos del servidor."""

    def __init__(self, opciones):
        self.op = opciones
        self.envios = {e["id"]: e for e in datos.generar_envios() if e["en_portal"]}
        self.sesiones = {}          # token -> búsquedas hechas con esa sesión
        self.busquedas_totales = 0
        self.rng = random.Random(opciones.semilla_fallos)
        self.cerrojo = threading.Lock()


class Manejador(BaseHTTPRequestHandler):
    portal = None  # se asigna al arrancar

    # --- utilidades -----------------------------------------------------
    def log_message(self, formato, *args):
        if self.portal.op.verbose:
            super().log_message(formato, *args)

    def responder(self, cuerpo, codigo=200):
        datos_bytes = cuerpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(datos_bytes)))
        self.end_headers()
        self.wfile.write(datos_bytes)

    def redirigir(self, destino, cookie=None):
        self.send_response(303)
        self.send_header("Location", destino)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def token(self):
        cookies = SimpleCookie(self.headers.get("Cookie", ""))
        return cookies["sesion"].value if "sesion" in cookies else None

    def sesion_valida(self):
        with self.portal.cerrojo:
            return self.token() in self.portal.sesiones

    def formulario(self):
        largo = int(self.headers.get("Content-Length", 0))
        crudo = self.rfile.read(largo).decode("utf-8")
        return {k: v[0] for k, v in parse_qs(crudo).items()}

    # --- rutas ----------------------------------------------------------
    def do_GET(self):
        ruta = self.path.split("?")[0]
        if ruta == "/salud":
            return self.responder("ok")
        if ruta == "/login":
            return self.responder(self.pagina_login())
        if not self.sesion_valida():
            return self.redirigir("/login")
        if ruta in ("/", "/inicio"):
            return self.responder(pagina("Inicio", "<h2>Bienvenido</h2><p>Use el menú para consultar sus envíos.</p>", menu=True))
        if ruta == "/envios":
            return self.responder(self.pagina_busqueda())
        self.responder(pagina("No encontrado", "<h2>Página no encontrada</h2>"), 404)

    def do_POST(self):
        ruta = self.path.split("?")[0]
        if ruta == "/login":
            return self.procesar_login()
        if ruta == "/envios/buscar":
            return self.procesar_busqueda()
        self.responder(pagina("No encontrado", "<h2>Página no encontrada</h2>"), 404)

    # --- login ----------------------------------------------------------
    def pagina_login(self, error=""):
        manual = "true" if self.portal.op.verificacion_manual else "false"
        aviso_error = f'<p class="aviso">{html.escape(error)}</p>' if error else ""
        cuerpo = f"""
<h2>Acceso clientes</h2>{aviso_error}
<form method="post" action="/login">
  <label for="usuario">Usuario</label><input type="text" id="usuario" name="usuario">
  <label for="contrasena">Contraseña</label><input type="password" id="contrasena" name="contrasena">
  <label><input type="checkbox" id="verificacion"> No soy un robot (verificación de demostración)</label>
  <input type="hidden" id="verificado" name="verificado" value="0">
  <button id="acceder" type="submit" disabled>Acceder</button>
  <p id="aviso-verificacion" class="aviso" hidden>Se requiere verificación adicional: resuelva el puzzle de imágenes.</p>
</form>
<script>
var MANUAL = {manual};
document.getElementById('verificacion').addEventListener('change', function () {{
  if (!this.checked) return;
  if (MANUAL) {{ document.getElementById('aviso-verificacion').hidden = false; return; }}
  document.getElementById('verificado').value = '1';
  document.getElementById('acceder').disabled = false;
}});
</script>"""
        return pagina("Acceso", cuerpo)

    def procesar_login(self):
        f = self.formulario()
        ok = (f.get("usuario") == self.portal.op.usuario
              and f.get("contrasena") == self.portal.op.contrasena
              and f.get("verificado") == "1"
              and not self.portal.op.verificacion_manual)
        if not ok:
            return self.responder(self.pagina_login("Usuario, contraseña o verificación incorrectos."), 200)
        token = secrets.token_hex(16)
        with self.portal.cerrojo:
            self.portal.sesiones[token] = 0
        self.redirigir("/inicio", cookie=f"sesion={token}; Path=/; HttpOnly")

    # --- búsqueda -------------------------------------------------------
    def pagina_busqueda(self, referencia="", mensaje=""):
        aviso = f'<p class="aviso">{html.escape(mensaje)}</p>' if mensaje else ""
        cuerpo = f"""
<h2>Consulta de envíos</h2>
<form id="form-busqueda" method="post" action="/envios/buscar">
  <label for="ref-envio">Número de envío</label>
  <input type="text" id="ref-envio" name="ref" value="{html.escape(referencia)}">
  <a id="btn-buscar" class="boton" href="#">Buscar</a>
</form>{aviso}
<script>
document.getElementById('btn-buscar').addEventListener('click', function (e) {{
  e.preventDefault();
  // Como en el portal original: el formulario se envía poco después del clic.
  setTimeout(function () {{ document.getElementById('form-busqueda').submit(); }}, 15);
}});
</script>"""
        return pagina("Consulta de envíos", cuerpo, menu=True)

    def pagina_ficha(self, envio):
        filas_inc = "".join(
            f"<tr><td>{fecha:%d/%m/%y %H:%M}</td><td>{html.escape(texto)}</td></tr>"
            for fecha, texto in envio["incidencias"]
        ) or '<tr><td colspan="2">Sin incidencias</td></tr>'
        asociado = (f'<p>Envío asociado: <span id="envio-asociado">{envio["asociado"]}</span></p>'
                    if envio["asociado"] else "")
        cuerpo = f"""
<h2>Detalle del envío</h2>
<label for="num-envio">Número de envío</label><input type="text" id="num-envio" value="{envio['id']}" readonly>
<label for="estado">Estado</label><input type="text" id="estado" value="" readonly>
<table>
  <tr><th>Admisión</th><td>{envio['admision']:%d/%m/%y %H:%M}</td></tr>
  <tr><th>Último movimiento</th><td>{envio['ultimo_movimiento']:%d/%m/%y %H:%M}</td></tr>
  <tr><th>Origen</th><td>{envio['origen']}</td></tr>
  <tr><th>Destino</th><td>{envio['destino']}</td></tr>
</table>{asociado}
<h3>Incidencias</h3><table>{filas_inc}</table>
<a id="btn-volver" class="boton" href="javascript:location.href='/envios'">Volver</a>
<script>
// La casilla Estado llega vacía y se rellena después, como en el portal original.
setTimeout(function () {{ document.getElementById('estado').value = {json.dumps(envio['estado'])}; }},
           {self.portal.op.retardo_estado_ms});
</script>"""
        return pagina("Detalle del envío", cuerpo, menu=True)

    def procesar_busqueda(self):
        p = self.portal
        referencia = self.formulario().get("ref", "").strip()
        with p.cerrojo:
            token = self.token()
            if token not in p.sesiones:
                caducada = True
            else:
                p.sesiones[token] += 1
                caducada = bool(p.op.caducar_tras) and p.sesiones[token] > p.op.caducar_tras
                if caducada:
                    del p.sesiones[token]
            p.busquedas_totales += 1
            numero = p.busquedas_totales
            lento = p.op.lento_prob > 0 and p.rng.random() < p.op.lento_prob
            if p.op.registro_consultas:
                with open(p.op.registro_consultas, "a", encoding="utf-8") as f:
                    f.write(referencia + "\n")

        if caducada:
            return self.redirigir("/login")
        if lento:
            time.sleep(p.op.lento_seg)
        if p.op.error_cada and numero % p.op.error_cada == 0:
            return self.responder(pagina("Error", "<h2>Error interno del servidor</h2><p>Inténtelo más tarde.</p>"), 500)

        envio = p.envios.get(referencia)
        if envio is None:
            return self.responder(self.pagina_busqueda(referencia, "No se encontraron envíos con esa referencia."))
        self.responder(self.pagina_ficha(envio))


class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # Una búsqueda lenta cuyo cliente ya se fue (reintentó) no es un error del portal.
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def main():
    parser = argparse.ArgumentParser(description="Portal de clientes de demostración (Meridiano Envíos).")
    parser.add_argument("--puerto", type=int, default=8765)
    parser.add_argument("--usuario", default=datos.USUARIO_DEMO)
    parser.add_argument("--contrasena", default=datos.CONTRASENA_DEMO)
    parser.add_argument("--lento-prob", type=float, default=0.0)
    parser.add_argument("--lento-seg", type=float, default=12.0)
    parser.add_argument("--error-cada", type=int, default=0)
    parser.add_argument("--caducar-tras", type=int, default=0)
    parser.add_argument("--verificacion-manual", action="store_true")
    parser.add_argument("--retardo-estado-ms", type=int, default=300)
    parser.add_argument("--semilla-fallos", type=int, default=7)
    parser.add_argument("--registro-consultas", help="archivo donde anotar cada número buscado")
    parser.add_argument("--verbose", action="store_true")
    op = parser.parse_args()

    Manejador.portal = EstadoPortal(op)
    servidor = Servidor(("127.0.0.1", op.puerto), Manejador)
    print(f"Portal de demostración en http://127.0.0.1:{op.puerto}  "
          f"(usuario '{op.usuario}', {len(Manejador.portal.envios)} envíos con registro)", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
