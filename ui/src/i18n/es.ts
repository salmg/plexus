/**
 * Español.
 *
 * Sobre la terminología, porque en este campo importa más que la gramática:
 *
 * - **APDU, PDOL, AFL, DOL, TLV, SFI, S(WTX)** no se traducen. Son nombres de
 *   estructuras de EMV y quien trabaja con ellas las lee en inglés.
 * - **terminal** es terminal, no «datáfono» ni «POS»: el puerto se llama así en
 *   el modelo y el nombre del puerto y el del aparato tienen que coincidir.
 * - **requester / responder** se traducen por lo que hacen — «pide» y
 *   «responde» — porque es así como se explican también en inglés.
 * - **sin contacto** en lugar de «contactless», y **tarjeta con contactos**
 *   para la de chip, que es la distinción que hace el propio atrium.
 * - **capture** es «captura»; **playbook** se queda en inglés porque es el
 *   nombre del formato de atrium y aparece en los ficheros.
 *
 * Lo que no está traducido aquí sale en inglés, no como una clave. Eso incluye
 * lo que dice el runtime de Python: llega ya escrito por el socket, y traducir
 * sólo la mitad de una traza sería peor que dejarla entera en un idioma.
 */

export const es: Record<string, string> = {
  // ── Barra superior ───────────────────────────────────────────────────
  "app.tagline": "lienzo de nodos para atrium",
  "app.by": "Diseñado y facilitado por academy.salmg.net",
  "topbar.undo": "Deshacer",
  "topbar.redo": "Rehacer",
  "topbar.open": "Abrir",
  "topbar.save": "Guardar",
  "topbar.new": "Nuevo",
  "topbar.console": "Consola",
  "topbar.inspector": "Inspector",
  "topbar.run": "Ejecutar",
  "topbar.stop": "Detener",
  "topbar.console.title": "Mostrar u ocultar la consola",
  "topbar.inspector.title": "Mostrar u ocultar el inspector",
  "topbar.runtime.title": "Dónde se ejecutaría el grafo realmente",
  "topbar.run.blocked": "Corrige primero los errores de la lista de comprobaciones",
  "topbar.run.ready": "Reservar los dispositivos y empezar",
  "topbar.theme.toLight": "Cambiar al modo claro",
  "topbar.theme.toDark": "Cambiar al modo oscuro",
  "topbar.language": "Idioma",
  "topbar.unreadable": "No se pudo leer el fichero: {message}",
  "count.errors_one": "1 error",
  "count.errors_other": "{count} errores",
  "count.warnings_one": "1 advertencia",
  "count.warnings_other": "{count} advertencias",

  // ── Paleta ───────────────────────────────────────────────────────────
  "palette.title": "Paleta",
  "palette.filter": "Filtrar nodos",
  "palette.empty": "Ningún nodo coincide con «{query}».",
  "palette.class.terminal": "Lado del terminal",
  "palette.class.transform": "Transformaciones",
  "palette.class.card": "Lado de la tarjeta",
  "palette.class.host": "Capa de host",
  "legend.cardPort": "puerto de tarjeta — pide una tarjeta",
  "legend.cardPort.title": "Pide: necesita una tarjeta al otro extremo",
  "legend.terminalPort": "puerto de terminal — es una tarjeta",
  "legend.terminalPort.title": "Responde: se comporta como lo haría una tarjeta",

  // ── Lienzo ───────────────────────────────────────────────────────────
  "canvas.pan": "Mover",
  "canvas.pan.title": "Arrastra el lienzo para desplazarlo",
  "canvas.select": "Seleccionar",
  "canvas.select.title":
    "Arrastra un recuadro para seleccionar nodos. El botón central o derecho sigue desplazando.",
  "canvas.tool": "Herramienta del ratón",
  "canvas.overview": "Vista general del grafo",
  "canvas.selected_one": "1 seleccionado",
  "canvas.selected_other": "{count} seleccionados",
  "canvas.delete": "Eliminar",
  "canvas.delete.title": "Eliminar los nodos seleccionados",
  "canvas.clear": "Quitar",
  "canvas.clear.title": "Quitar la selección",
  "canvas.empty.title": "Arrastra un nodo desde la paleta.",
  "canvas.empty.hint":
    "Una cadena va de izquierda a derecha: algo que pide, las transformaciones que quieras y, al final, algo que responde.",
  "canvas.refused": "Esa conexión no está permitida.",
  "canvas.apdu_one": "1 apdu",
  "canvas.apdu_other": "{count} apdu",

  // ── Tarjeta de nodo ──────────────────────────────────────────────────
  "node.unknown": "Tipo desconocido",
  "node.noDevice": "sin dispositivo",
  "node.budget": "{ms} ms previstos",
  "node.errors": "{count} err",
  "node.status.idle": "sin reservar",
  "node.status.ready": "reservado, sin tráfico",
  "node.status.live": "hay tráfico ahora",
  "node.status.error": "con fallo — mira las comprobaciones",

  // ── Inspector ────────────────────────────────────────────────────────
  "inspector.title": "Inspector",
  "inspector.graph": "Grafo",
  "inspector.nodes_one": "1 nodo",
  "inspector.nodes_other": "{count} nodos",
  "inspector.name": "Nombre",
  "inspector.roundTrip": "{ms} ms ida y vuelta",
  "inspector.hint":
    "Selecciona un nodo para configurarlo. Las estimaciones de arriba son la suma del coste declarado de cada nodo, no una medición.",
  "inspector.label": "Etiqueta",
  "inspector.id": "id",
  "inspector.cost": "coste",
  "inspector.cost.value": "{ms} ms por trayecto",
  "inspector.seen": "visto",
  "inspector.seen.value": "{count} apdu",
  "inspector.seen.withMean": "{count} apdu, {ms} ms de media",
  "inspector.caveats": "Conviene saber",
  "inspector.enable": "Activar",
  "inspector.disable": "Desactivar",
  "inspector.enableAll": "Activar todos",
  "inspector.disableAll": "Desactivar todos",
  "inspector.delete": "Eliminar",
  "inspector.deleteCount": "Eliminar {count}",
  "inspector.close": "Cerrar el inspector",
  "inspector.multi.hint":
    "Arrastra cualquiera de ellos para mover el grupo. Al eliminarlos se van también sus cables.",
  "inspector.unknownType":
    "Este grafo contiene un nodo de tipo «{type}», que esta versión no conoce.",
  "inspector.checks": "Comprobaciones",
  "inspector.checks.none": "Nada que corregir. Este grafo se puede armar.",
  "field.chooseDevice": "Elige un dispositivo",
  "field.notPresent": "{name} — no está presente",
  "field.noDevices": "El runtime no ve ningún dispositivo {kind}.",
  "field.hexOnly": "Sólo dígitos hexadecimales.",

  // ── Consola ──────────────────────────────────────────────────────────
  "console.title": "Consola",
  "console.streamOn": "Traza activa",
  "console.streamOff": "Traza parada",
  "console.clear": "Limpiar",
  "console.banner": 'Consola de Plexus. Escribe "help".',

  // ── Revisión al abrir ────────────────────────────────────────────────
  "import.title": "Revisar este grafo",
  "import.heading": "¿Cargar «{name}»?",
  "import.willDo": "Si lo armas, hará esto",
  "import.claim": "reservar",
  "import.dial": "conectar con",
  "import.touch": "leer o escribir",
  "import.nothing":
    "No reserva ningún dispositivo, no conecta con nada y no nombra ningún fichero.",
  "import.reassure":
    "Cargarlo sólo lo dibuja. No se reserva ni se conecta nada hasta que pulses Ejecutar, y el runtime limita cada ruta a su espacio de trabajo.",
  "import.cancel": "Cancelar",
  "import.load": "Cargar",

  // ── Comprobaciones ───────────────────────────────────────────────────
  "check.empty": "El grafo está vacío. Arrastra un nodo de la paleta para empezar.",
  "check.unknownType":
    "Tipo de nodo desconocido «{type}». Este grafo se guardó con otra versión.",
  "check.unwiredRequester":
    "{node} no tiene nada en su puerto {port}. Conéctalo a algo que responda.",
  "check.unwiredResponder": "Nadie le pregunta nada a {node}. Se quedará inactivo.",
  "check.unboundDevice": "{node} no tiene ningún dispositivo seleccionado.",
  "check.deviceMissing": "{name} no está presente. Conéctalo o elige otro.",
  "check.deviceContended":
    "{nodes} reclaman los dos {name}. Un mismo dispositivo no puede ser objetivo e iniciador a la vez: lo que sale de intentarlo es un tiempo de espera agotado, no un mensaje.",
  "check.unpairedLink":
    "Esta arista cruza de máquina y lleva tráfico de tarjeta sin autenticar. Pon un secreto de emparejamiento en {node}, o túnelalo por SSH.",
  "check.tapBudget.fail":
    "Unos {ms} ms por intercambio, ida y vuelta. Un kernel sin contacto abandonará la transacción mucho antes de eso.",
  "check.tapBudget.warn":
    "Unos {ms} ms por intercambio, ida y vuelta. Cerca de lo que tolera un kernel sin contacto: espera acercamientos perdidos.",

  // ── Por qué se rechaza un cable ──────────────────────────────────────
  "connect.self": "Un nodo no puede hablar consigo mismo.",
  "connect.unknownNode": "El nodo no está en este grafo.",
  "connect.unknownPort": "Puerto desconocido.",
  "connect.wrongChannel": "Un puerto {from} no transporta {to}.",
  "connect.bothAsk":
    "Los dos puertos piden. Una arista une uno que pide con uno que responde.",
  "connect.bothAnswer":
    "Los dos puertos responden. Una arista une uno que pide con uno que responde.",
  "connect.alreadyWired": "El puerto {port} de {node} ya está conectado.",
  "connect.loop": "Eso cerraría un bucle.",

  // ── Puertos ──────────────────────────────────────────────────────────
  "port.card": "tarjeta",
  "port.terminal": "terminal",
  "port.oob": "fuera de banda",

  // ── Nodos: lado del terminal ─────────────────────────────────────────
  "node.simtrace2.title": "SimTrace2",
  "node.simtrace2.summary":
    "Presenta una tarjeta virtual a un terminal real por la interfaz de contactos.",
  "node.simtrace2.caveat.0":
    "El demonio se ejecuta fuera de Plexus. Este nodo comprueba que esté levantado y se conecta a él.",
  "field.simtrace2.device.label": "Dispositivo",
  "field.simtrace2.assertOnly.label": "No empezar si el demonio no está ya levantado",
  "field.simtrace2.assertOnly.help":
    "simtrace2-remsim necesita acceso USB directo y se arranca a mano en una consola de root. Plexus nunca lo lanza.",

  "node.acr122u.target.title": "ACR122U como tarjeta",
  "node.acr122u.target.summary":
    "Emula una tarjeta sin contacto ante el terminal en cuyo campo está.",
  "node.acr122u.target.caveat.0":
    "El PN532 fuerza el primer byte del UID a 0x08, la marca de UID aleatorio. Un terminal que exija un UID concreto no verá el de la tarjeta.",
  "node.acr122u.target.caveat.1":
    "Una trama por intercambio: 253 bytes para la respuesta reenviada. Más largo necesita encadenado 14443-4, que no está implementado.",
  "node.acr122u.target.caveat.2":
    "Cada APDU cuesta una ida y vuelta por USB. Los kernels sin contacto imponen un límite de tiempo y pueden abandonar la transacción.",
  "field.acr122u.target.device.label": "Lector",
  "field.acr122u.target.splitResponses.label": "Ofrecer las respuestas largas como 61 XX",
  "field.acr122u.target.splitResponses.help":
    "Permite que el terminal recoja una respuesta demasiado grande con GET RESPONSE en vez de fallar la trama.",

  "node.vpcd.title": "Lector virtual",
  "node.vpcd.summary":
    "Presenta una tarjeta a cualquier cosa de esta máquina que hable PC/SC, mediante vpcd.",
  "node.vpcd.caveat.0": "Necesita virtualsmartcard, con vpcd cargado en pcscd.",
  "field.vpcd.host.label": "Host de vpcd",
  "field.vpcd.port.label": "Puerto de vpcd",

  "node.agent.title": "Agente EMV",
  "node.agent.summary":
    "Un terminal sintético: el modelo escribe sus propios comandos y lee las respuestas.",
  "node.agent.caveat.0": "Maneja la tarjeta por su cuenta. Apúntalo a una tarjeta tuya.",
  "field.agent.task.label": "Tarea",
  "field.agent.task.placeholder": "Enumera las aplicaciones y lee todos los registros",
  "field.agent.provider.label": "Modelo",
  "option.agent.provider.anthropic": "Anthropic",
  "option.agent.provider.openai": "Compatible con OpenAI",
  "option.agent.provider.local": "Local (Ollama, vLLM, llama.cpp)",
  "field.agent.bruteSfi.label": "Recorrer los SFI por fuerza bruta",

  "node.probe.title": "Sonda de huella",
  "node.probe.summary":
    "Ejecuta la secuencia de huella de atrium y registra lo que responde la tarjeta.",
  "field.probe.depth.label": "Profundidad",
  "option.probe.depth.quick": "Rápida — barrido de AID y ATR/ATS",
  "option.probe.depth.standard": "Normal — además registros y DOL",
  "option.probe.depth.deep": "Profunda — además barrido de SFI",

  // ── Nodos: lado de la tarjeta ────────────────────────────────────────
  "node.pcsc.title": "Lector PC/SC",
  "node.pcsc.summary":
    "Una tarjeta con contactos en un lector conectado a esta máquina.",
  "field.pcsc.device.label": "Lector",

  "node.acr122u.reader.title": "Campo del ACR122U",
  "node.acr122u.reader.summary":
    "Una tarjeta sin contacto dentro del campo de radio de un ACR122U.",
  "node.acr122u.reader.caveat.0":
    "Un ACR122U no puede ser objetivo e iniciador a la vez. Emular y leer necesita dos.",
  "field.acr122u.reader.device.label": "Lector",

  "node.nfcgate.title": "Sesión de NFCGate",
  "node.nfcgate.summary":
    "Una tarjeta en el campo de un móvil Android, alcanzada a través de un servidor NFCGate.",
  "node.nfcgate.caveat.0":
    "Aquí la latencia es el móvil, la Wi-Fi y el servidor. Es el mayor coste de casi cualquier grafo.",
  "field.nfcgate.host.label": "Host del servidor",
  "field.nfcgate.port.label": "Puerto del servidor",
  "field.nfcgate.session.label": "Sesión",
  "field.nfcgate.cafile.label": "Certificado de la CA",
  "field.nfcgate.cafile.placeholder": "opcional, para un servidor con TLS",
  "field.nfcgate.cafile.help":
    "Déjalo vacío para un servidor en claro en la red local.",

  "node.cardproxy.title": "Tarjeta remota",
  "node.cardproxy.summary":
    "Un lector en otra máquina, alcanzado por el proxy de tarjeta de atrium.",
  "field.cardproxy.host.label": "Host del proxy",
  "field.cardproxy.port.label": "Puerto del proxy",
  "field.cardproxy.pairing.label": "Secreto de emparejamiento",
  "field.cardproxy.pairing.help":
    "Sin esto el enlace va sin autenticar. Mejor déjalo en loopback o pásalo por un túnel SSH.",

  "node.capture.title": "Captura grabada",
  "node.capture.summary":
    "Reproduce una sesión guardada. Sin hardware, sin tarjeta, repetible.",
  "field.capture.path.label": "Fichero de captura",
  "field.capture.strict.label": "Reproducción estricta",
  "field.capture.strict.help":
    "Fallar ante un comando que la captura no contiene, en vez de improvisar una respuesta.",

  // ── Nodos: transformaciones ──────────────────────────────────────────
  "node.mutation.title": "Mutación",
  "node.mutation.summary":
    "Reescribe PDOL, AFL, DOL y etiquetas de respuesta al pasar la conversación.",
  "field.mutation.playbook.label": "Playbook",
  "field.mutation.logMutations.label": "Registrar cada reescritura",

  "node.inject.title": "Inyección",
  "node.inject.summary":
    "Dispara comandos extra contra la tarjeta alrededor de una instrucción disparadora.",
  "node.inject.caveat.0":
    "El comando inyectado llega a la tarjeta fuera de banda, así que gasta la ida y vuelta dos veces. Gratis en contactos, y suficiente para agotar el tiempo de un acercamiento sin contacto.",
  "field.inject.triggerIns.label": "INS disparadora",
  "field.inject.when.label": "Cuándo",
  "option.inject.when.before_command": "Antes del comando",
  "option.inject.when.after_response": "Después de la respuesta",
  "field.inject.apdu.label": "APDU a inyectar",
  "field.inject.repeat.label": "Disparar siempre",

  "node.trace.title": "Sonda de traza",
  "node.trace.summary": "Decodifica TLV y registra el intercambio. No cambia nada.",
  "field.trace.detail.label": "Detalle",
  "option.trace.detail.raw": "Sólo los bytes en crudo",
  "option.trace.detail.tlv": "TLV decodificado",
  "option.trace.detail.annotated": "Decodificado y anotado",
  "field.trace.path.label": "Escribir en",

  "node.swtx.title": "Temporización",
  "node.swtx.summary":
    "Pide más tiempo al terminal con S(WTX) cuando la tarjeta va lenta.",
  "node.swtx.caveat.0":
    "Gana tiempo en contactos. Un kernel sin contacto puede negarlo y tirar la transacción.",
  "field.swtx.wtxm.label": "Multiplicador WTX",
  "field.swtx.thresholdMs.label": "Pedirlo por encima de (ms)",

  "node.breakpoint.title": "Punto de parada",
  "node.breakpoint.summary":
    "Retiene cada APDU que coincida hasta que lo liberes desde la consola.",
  "node.breakpoint.caveat.0":
    "Para el reloj en seco. Cualquier terminal real agotará su tiempo de espera mientras piensas.",
  "field.breakpoint.matchIns.label": "Parar en la INS",
  "field.breakpoint.matchIns.placeholder": "vacío para todos los comandos",
  "field.breakpoint.editable.label": "Permitir editar los bytes retenidos",
};
