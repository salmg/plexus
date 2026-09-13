# Instalación

Un comando lo levanta todo. Otros dos lo traen antes.

```bash
git clone --recurse-submodules https://github.com/salmg/plexus
cd plexus
pip install -e ".[runtime]"
plexus up
```

La primera vez compila el editor, lo que tarda un minuto y necesita red una
sola vez. Después de eso:

```
The editor has not been built here yet. Building it once:
    npm install
    npm run build:bundle

    ...

Plexus on http://127.0.0.1:8900/
The editor and the runtime are both here. Ctrl-C to stop.
```

Se abre un navegador ahí. La barra superior pone `127.0.0.1:8900/ws` en vez de
**simulated**, que es como sabes qué hay detrás de **Ejecutar**: un runtime capaz
de tomar hardware de verdad, no el simulador que el editor lleva por su cuenta.

Abrir la página no arma nada. **Ejecutar** es lo único que arma un grafo, y es
un botón que pulsas tú.

## Qué hace falta

- **Python 3.8 o posterior** — el runtime, las comprobaciones y la CLI.
- **Node 22.12 o posterior** — para compilar el editor, una vez. Con 20.19+
  se compila todo menos `npm run export:catalogue`, que importa TypeScript
  directamente y quiere 22.18.
- **Un lector, un teléfono o un SimTrace2** — sólo para los nodos que tocan
  hardware, y sólo cuando armes uno. Lo demás funciona en un portátil.

`vendor/atrium` es un submódulo de git, por eso el clon de arriba lleva
`--recurse-submodules`. Si ya lo clonaste sin él:

```bash
git submodule update --init vendor/atrium
```

Está fijado a un commit y **nunca se edita**. Actualizarlo es un paso explícito
y revisable:

```bash
git submodule update --remote vendor/atrium
```

Si tu copia de atrium vive en otro sitio, dilo y no cambia nada más:

```bash
export PLEXUS_ATRIUM=/path/to/atrium
```

## Qué ha hecho `plexus up`

Tres cosas, y cada una puedes hacerla tú.

**Ha compilado el editor**, porque esta copia aún no tenía ninguno — `npm
install` y `npm run build:bundle`, dentro de `ui/`. Imprime los dos comandos
antes de ejecutarlos, porque el primero sale a la red. Con
`plexus up --no-build` no ejecuta npm en ningún caso, y te dice qué ejecutar.

**Ha servido las dos mitades desde un mismo origen.** El editor son ficheros
estáticos; el runtime es `/ws` y `/api/*` al lado. Compartir origen es lo que
quita la configuración — ni CORS, ni contenido mixto, ni lista de orígenes, ni
URL que pegar — y es como el editor encuentra su runtime sin que se lo digan.

**Ha abierto un navegador.** Con `--no-open` no lo abre; `--host` y `--port` lo
mueven.

Escuchar en cualquier dirección que no sea loopback **se niega a arrancar** sin
un token — ver [What it refuses](../security.md).

## El editor por su cuenta

El editor no necesita Python ni runtime. Trae un simulador, así que puedes
dibujar un montaje, ejecutarlo y leer la traza sin instalar nada más. Para
trabajar en el propio editor es la mejor herramienta, porque recarga mientras
escribes:

```bash
cd ui
npm install
npm run dev
```

Abre <http://localhost:5173> y elige **Español** en la barra superior. Deberías
ver el montaje de ejemplo, y la barra superior debería decir **simulated**:

![El editor en español](../images/03-espanol.png)

### Comprobar que funciona

Pulsa **Ejecutar**. El simulador empuja una transacción sin contacto plausible
por la cadena que haya dibujada: verás subir los contadores de los nodos,
ponerse verdes las aristas y correr la traza en la consola:

![Un grafo en marcha](../images/08-running.png)

Pulsa **Detener**. Si eso funcionó, el editor está instalado.

> Los APDU que manda el simulador son *ilustrativos, no una captura*. Las
> estructuras tienen la forma de las de verdad, para que la traza se lea bien a
> quien conoce EMV, pero ninguna tarjeta emitió esos bytes y no hay que concluir
> nada de ellos.

Para apuntar ese servidor de desarrollo a un runtime de verdad, arranca uno y
abre la URL que imprime:

```bash
plexus serve
```

```
Plexus runtime on ws://127.0.0.1:8900/ws
Point the editor at it:  http://localhost:5173/?runtime=ws://127.0.0.1:8900/ws
```

Esa es la forma de dos procesos, y sigue siendo lo que `npm run dev` necesita.
`plexus up` existe para que nadie tenga que pasar por ahí para empezar.

## El runtime por su cuenta

El modelo, las comprobaciones y la CLI no necesitan más que PyYAML — ni Node, ni
navegador, ni editor:

```bash
pip install -e .
plexus nodes
```

```
   capture            card       terminal(answers)                  Recorded capture
 · pcsc               card       terminal(answers)                  PC/SC reader
   probe              terminal   card(asks)                         Fingerprint probe
   mutation           transform  terminal(answers) card(asks)       Mutation
   ...

· needs hardware or a daemon this build cannot start; the rest run anywhere.
```

El punto es la parte honesta: esos nodos se resuelven dentro de un grafo y se
niegan al armarlo, diciendo qué les falta.

> La CLI y el runtime hablan **en inglés**. Lo que dicen llega ya escrito por el
> socket, y traducir media traza sería peor que dejarla entera en un idioma.
> Ver [Idiomas](../languages.md) (en inglés).

Ahora ejecuta un montaje que no necesita nada:

```bash
plexus run examples/probe-a-capture.plexus.yaml
```

```
info  Armed 3 nodes: probe-1 -> mutation-1 -> capture-1
    31.2 -> 00A404000E325041592E5359532E444446303100
    32.6 <- 6F20840E325041592E5359532E4444463031A50E…9000  1.4 ms
    ...
    34.3 <- 70145A0854133300123456785F24032512315F3401019000  0.0 ms
    34.4 <- 70145A0854133300123456785F24032512315F3401079000  rewritten by mutation-1

4 exchanges, 0 errors.
```

Esas dos últimas líneas son lo importante: lo que dijo la tarjeta y lo que le
contaron al terminal. La reescritura la hace el motor de mutaciones de atrium,
no una imitación.

## Opcional: las pruebas

```bash
pip install -e ".[dev]"
pytest
```

Cubren el modelo, las comprobaciones, el motor de punta a punta contra una
captura grabada, el protocolo WebSocket, lo que recibe quien instala y un clon
esconde, y cada agujero que se ha encontrado y cerrado. El número no se pone
aquí a propósito — puso 90 durante mucho tiempo después de ser 212, y
`pytest -q` lo contesta en un segundo.

---

## Notas por plataforma

**Windows.** Todo lo anterior funciona. Para el runtime, ojo: Git Bash **no
puede ejecutar** un Python de la Microsoft Store — sale `Permission denied`, que
se lee exactamente igual que si no estuviera instalado. Usa PowerShell, o un
Python de python.org.

**Móviles y tabletas.** El editor se adapta. Por debajo de 1100px el inspector
se abre sobre el lienzo en vez de al lado, y por debajo de 760px también lo
hacen la paleta y la consola — de una en una, y se quitan tocando el lienzo o
con `Esc`. La barra superior pliega sus controles en un menú cuando dejan de
caber, y Ejecutar nunca deja de caber. El lienzo hace zoom con dos dedos, con la
rueda, con un toque doble o con los botones de la esquina.

**La mitad del hardware necesita Linux.** No es cosa de Plexus: ahí viven
`pcscd`, `vpcd` y `simtrace2-remsim`, y la propia guía de atrium lo da por
supuesto. Dibujar y comprobar un grafo se hace en cualquier sitio; armarlo, allí
donde estén los lectores.

**Bancos de trabajo sin red.** El editor no descarga nada en ejecución: ni
fuentes web, ni iconos de CDN, ni analítica. `npm install` es el único paso que
necesita red, y se puede hacer en otra máquina y copiar — igual que el
`plexus/web` ya compilado, que es todo lo que `plexus up` busca.

---

Siguiente: **[Tu primer montaje](first-run.md)**.

---

Diseñado y facilitado por **[academy.salmg.net](https://academy.salmg.net)**.
