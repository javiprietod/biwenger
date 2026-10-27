# Panel Fantasy NBA para Biwenger

Script que descarga los datos de tu liga NBA de Biwenger con tu propia sesión y genera `panel.html`: mercado, saldo y valor de equipo de cada usuario, variaciones diarias y semanales, plantillas, subidas/bajadas y últimos movimientos.

## 1. Requisitos

Python 3.8 o superior. No hace falta instalar nada más.

- **Windows:** instálalo desde python.org y marca «Add Python to PATH».
- **Mac:** abre Terminal y escribe `python3 --version`. Si no lo tienes, el sistema te ofrecerá instalarlo.

## 2. Configuración

1. Pon esta carpeta donde quieras (por ejemplo `Documentos/biwenger-nba`).
2. Copia `config.example.json` y renómbralo a `config.json`.
3. Rellena **una** de estas dos opciones:
   - **Email y contraseña** de Biwenger (si entras con email).
   - **Token**, si entras con Google o Apple:
     1. Abre biwenger.as.com en Chrome, con tu sesión iniciada.
     2. Pulsa F12 → pestaña **Red** (Network) → filtra por `api/v2` y recarga la página.
     3. Haz clic en cualquier petición → **Encabezados** → busca `Authorization: Bearer ...`.
     4. Copia lo que va después de `Bearer` en el campo `token`.

`config.json` contiene tu acceso a Biwenger: no lo compartas ni lo subas a ningún sitio.

Si tienes varias ligas, el script elige la de NBA. Para forzar una concreta, pon su número en `liga_id`.

## 3. Uso

Abre una terminal en la carpeta y ejecuta:

```
python biwenger_nba.py --abrir
```

(En Mac, `python3` en lugar de `python`.)

Genera `panel.html` y lo abre en el navegador. Otras opciones:

- `--offline` regenera el panel con lo último descargado, sin conectarse.

En cada descarga también consulta en RotoWire a los jugadores que Biwenger marca como lesionados. En la columna «Estado» verás su estado con color (rojo Out, naranja Doubtful, amarillo Questionable, verde Probable) y la fecha estimada de vuelta; pasa el ratón por encima para leer la última noticia o haz clic para abrir su ficha. Para ver solo el parte de lesiones en la terminal: `python lesiones_rotowire.py`.

## 4. Ejecutarlo cada día automáticamente

Biwenger actualiza los precios de madrugada, así que conviene ejecutarlo por la mañana. Cada ejecución guarda una foto en `datos/historial/`, que es lo que alimenta el gráfico de evolución y la variación semanal.

**Windows** (en una terminal, cambiando la ruta por la tuya):

```
schtasks /create /tn "Panel Biwenger NBA" /sc daily /st 09:15 /tr "python C:\Users\TU_USUARIO\Documents\biwenger-nba\biwenger_nba.py"
```

**Mac** (en Terminal, `crontab -e` y añade esta línea con tu ruta):

```
15 9 * * * /usr/bin/python3 /Users/TU_USUARIO/Documents/biwenger-nba/biwenger_nba.py
```

El ordenador tiene que estar encendido a esa hora; si no, ejecútalo a mano cuando lo enciendas.

**En GitHub Actions** (sin depender del ordenador): `.github/workflows/panel.yml` regenera el panel cada día a las 8:00 de Madrid, guarda el historial en el repo, publica el panel en GitHub Pages y envía un correo con el enlace. Necesita estos secrets del repositorio:

- `BIWENGER_CONFIG`: el contenido completo de tu `config.json` (`gh secret set BIWENGER_CONFIG < config.json`).
- `MAIL_USERNAME` y `MAIL_PASSWORD`: cuenta de Gmail y su contraseña de aplicación.
- `MAIL_TO`: destinatarios del correo, separados por comas.

Para lanzarlo a mano: pestaña **Actions** → «Panel diario» → **Run workflow**, o `gh workflow run panel.yml`. También puedes usar el botón **Actualizar** del propio panel: la primera vez te pide un token de GitHub *fine-grained* (solo este repositorio, permiso **Actions: Read and write**) y lo guarda únicamente en ese navegador. Mayús+clic en el botón lo olvida. El repositorio y el workflow que lanza están en las constantes `GITHUB_REPO`, `GITHUB_WORKFLOW` y `GITHUB_BRANCH` al principio de `biwenger_nba.py`.

## 5. Rentabilidad

La sección «Mejores rentabilidades» ordena a los jugadores por puntos por millón de valor (Pts/M), esta temporada o la pasada, con un mínimo de media o de puntos para que no la copen los jugadores de 150 k€ que casi no juegan. Para la temporada pasada compara sus puntos con el valor que tenía hace un año: «Vs. esperado» indica cuántos puntos hizo por encima o por debajo de lo normal para ese valor, y «Ganga» marca a los que rindieron por encima y ahora valen menos.

Biwenger solo da el valor de hace un año jugador a jugador y corta enseguida con error 429 si se piden muchos. Por eso cada ejecución descarga como mucho 60 (empezando por los que más puntos hicieron) y los guarda en `datos/precios_hace_un_ano.json`. Los ~480 jugadores se completan en unos días. Se ajusta con `YEAR_AGO_MAX_PER_RUN` y `YEAR_AGO_DELAY` al principio de `biwenger_nba.py`.

## 6. Qué es exacto y qué es estimado

- **Exacto:** tu saldo, plantillas, valores, variaciones, mercado y clasificación.
- **Puja máxima:** saldo + 25 % del valor del equipo − pujas abiertas. La tuya es la que da Biwenger; la de los rivales sale de su saldo estimado y no descuenta sus pujas abiertas, que no son públicas. En «¿Y si vende…?» puedes marcar jugadores de cualquier usuario y ver cómo cambiaría su puja máxima: vender por su valor la sube un 75 % de ese valor (entra el dinero, pero el equipo vale menos). Si tu liga usa otro porcentaje, cámbialo con `puja_max_pct` en `config.json` (por ejemplo `0.25`).
- **Estimado:** el saldo de los rivales. Biwenger no lo publica, así que se calcula con los fichajes, ventas y primas por jornada del tablón de la liga, tomando tu saldo real como referencia. Solo cuenta lo ocurrido desde el último reinicio de la liga.
- Tras un reinicio cada uno recibe un equipo distinto, así que lo que todos comparten es el **patrimonio** (saldo + valor del equipo), no el saldo. El valor de cada equipo al reiniciar se guarda en `datos/inicio.json` la primera vez que se ejecuta el script; no lo borres. Si conoces el patrimonio inicial (contando las primas del primer día), ponlo en `saldo_inicial`: sirve de control frente a tu saldo y de respaldo si tu saldo no se puede descargar.

## 7. Si algo falla

- **Error 401/403:** sesión inválida. Revisa email y contraseña o copia de nuevo el token.
- **Error 400 o 426:** Biwenger pide una versión más reciente. En la pestaña Red del navegador, mira la cabecera `X-Version` de cualquier petición y ponla en `x_version`.
- **El panel muestra avisos arriba:** alguna parte no se pudo descargar. El resto sigue funcionando.
- **Posiciones raras (P1, P2…):** ajusta el bloque `posiciones` en `config.json`.

Los datos descargados quedan en `datos/raw/` y no contienen tu contraseña ni tu token. Si algo se ve mal, esos archivos sirven para depurarlo.
