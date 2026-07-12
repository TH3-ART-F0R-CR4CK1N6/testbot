# Universal Search OSINT Bot

Bot de Telegram escrito en Python para consultar información disponible
públicamente sobre usernames, dominios y direcciones IP.

## Funciones

- Detección automática del tipo de objetivo.
- Búsqueda de usernames en perfiles públicos.
- Resolución DNS de dominios.
- Consulta de registros RDAP de dominios.
- DNS inverso y RDAP de direcciones IP.
- Bloqueo de consultas sobre IP privadas, locales y reservadas.
- Límite de consultas por usuario.
- Ejecución aislada mediante Docker.
- Contenedor sin privilegios y sistema de archivos de solo lectura.
- No almacena objetivos ni resultados.

## Estructura

```text
universal-search-bot/
├── bot.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .gitignore
└── README.md
```

## Requisitos

- Docker
- Docker Compose
- Un bot creado con BotFather

## Crear el bot de Telegram

1. Abre Telegram.
2. Busca `@BotFather`.
3. Ejecuta `/newbot`.
4. Introduce el nombre y username del bot.
5. Copia el token generado.

No compartas ni publiques ese token.

## Configuración

Copia el archivo de ejemplo:

```bash
cp .env.example .env
```

Edita `.env`:

```env
TELEGRAM_BOT_TOKEN=TU_TOKEN_REAL
RATE_LIMIT=10
REQUEST_TIMEOUT=12
LOG_LEVEL=INFO
```

## Ejecutar con Docker

Construye e inicia el contenedor:

```bash
docker compose up --build -d
```

Consulta los logs:

```bash
docker compose logs -f bot
```

Comprueba el estado:

```bash
docker compose ps
```

Reinicia el bot:

```bash
docker compose restart bot
```

Detén el bot:

```bash
docker compose down
```

Reconstruye el contenedor después de modificar el código:

```bash
docker compose up --build -d
```

## Comandos de Telegram

```text
/start
/help
/search <objetivo>
/username <username>
/domain <dominio>
/ip <dirección IP>
/privacy
```

## Ejemplos

```text
/search torvalds
/username torvalds
/domain example.com
/ip 1.1.1.1
```

## Fuentes utilizadas

El bot consulta fuentes públicas:

- GitHub
- GitLab
- Reddit
- Keybase
- DEV
- Codeberg
- Cloudflare DNS over HTTPS
- RDAP.org
- DNS inverso del sistema

La disponibilidad de una fuente puede cambiar o aplicar límites propios.

## Solución de problemas

### Falta el token

Si aparece un error relacionado con `TELEGRAM_BOT_TOKEN`, comprueba que:

1. El archivo se llame exactamente `.env`.
2. Se encuentre junto a `docker-compose.yml`.
3. El token no tenga espacios o comillas adicionales.

### El token es inválido

Genera o revoca tokens desde `@BotFather` y reemplaza el valor en `.env`.

Después reinicia:

```bash
docker compose down
docker compose up -d
```

### Ver el error completo

```bash
docker compose logs --tail=200 bot
```

### El bot no responde

Comprueba que solo exista una instancia utilizando el token:

```bash
docker compose ps
docker compose logs --tail=100 bot
```

Telegram no permite que varias instancias usen polling simultáneamente con el
mismo token.

## Seguridad

- No publiques el archivo `.env`.
- Revoca el token inmediatamente si se filtra.
- No uses el bot para acoso, vigilancia o acceso no autorizado.
- Un username coincidente no demuestra que dos perfiles pertenezcan a la misma
  persona.
- Los resultados deben verificarse manualmente.
- Las direcciones IP pueden ofrecer ubicaciones aproximadas, no domicilios
  exactos.

## Uso responsable

Este proyecto está diseñado para investigación legítima con información
pública. El operador es responsable de cumplir la legislación aplicable,
las condiciones de cada fuente y las reglas de Telegram.
