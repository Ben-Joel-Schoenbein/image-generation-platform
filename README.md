# Friends Image Studio

Self-hosted image generation and image cataloguing for a small friend group. It now has two sites: an image generator and a separate image library for organizing files into nested groups such as comic series, issues, and pages. It also includes optional Discord commands and configurable prompt rules.

## What this starter includes

- Text-to-image and image-to-image forms in the website.
- Admin-created friend accounts; generated images are private to each account.
- Admin controls to enable or disable each generation mode, set a prompt length limit, and block a list of words or phrases.
- Optional Discord commands `/imagine` and `/edit`, available to members of any server where you invite the bot; no server-ID whitelist is needed.
- ComfyUI and model files stay on the OpenStack instance. The ComfyUI port is only reachable on the private Docker network.
- Caddy terminates HTTPS for the website when the domain points to the instance.
- A separate React/TypeScript image-library site with an Apollo Server GraphQL API and PostgreSQL metadata database.
- Collections can be nested (for example, a series containing issues); images can be titled, described, reordered, removed from a group, or deleted.
- Image binaries are stored on the server's `media-files/` directory; Postgres stores collection structure and image metadata. Uploads accept PNG, JPEG, and WebP.
- The library site is protected by a shared HTTP Basic Auth login for your group. Postgres and the media API have no public host ports.

## Important limitation of the policy filter

The configurable rules inspect the text prompt and reject a matching word or phrase. They do **not** understand every synonym, infer the meaning of a prompt, or scan generated pixels for prohibited content. A prompt filter alone cannot guarantee what appears in an image. Use the service with people you trust; if you need meaning-based or output-image moderation, add a classifier and test its false positives and misses before sharing the service more broadly.

## OpenStack instance

Create an Ubuntu 24.04 instance with an NVIDIA GPU attached and enough persistent disk for the model, Docker images, output images, and backups. Use a GPU flavor supported by the model workflow you choose. Qwen Image Edit is a much larger model than SDXL; check the current model card and ComfyUI workflow for memory needs before selecting a flavor.

The default ComfyUI container base uses PyTorch with CUDA 12.8. If your OpenStack GPU needs a newer CUDA/PyTorch build, set `PYTORCH_BASE` in `.env` to a compatible official PyTorch runtime image before building. ComfyUI's current installation guidance recommends a recent PyTorch build and CUDA 13.0 or newer for NVIDIA 20-series GPUs and later; match the image to the GPU passed through by OpenStack.

In the OpenStack security group, allow:

- TCP 22 only from your own public IP (for SSH administration).
- TCP 80 and 443 from the people who need the site (or the internet if you want it reachable from anywhere).

Do not open TCP 8000 or 8188. The website API and ComfyUI are not published on host ports by Compose.

Install [Docker Engine for Ubuntu](https://docs.docker.com/engine/install/ubuntu/) and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) using their official installation guides. Verify `nvidia-smi` works on the host and that Docker can see the GPU before starting this stack.

## First start

1. Point DNS A records (and AAAA records only if IPv6 is configured) for both `DOMAIN` and `LIBRARY_DOMAIN` at the instance's public IP.
2. Copy `.env.example` to `.env`. Set the two hostnames, the generator admin username and password, and `IMAGE_STUDIO_URL`. Generate `SESSION_SECRET`, `MEDIA_PROXY_SECRET`, and `POSTGRES_PASSWORD` with separate `openssl rand -hex 32` commands. Keep `.env` private.
3. Make a shared password for friends to open the library site, then create its Caddy hash:

   ```bash
   docker run --rm caddy:2-alpine caddy hash-password --plaintext 'your-shared-password'
   ```

   Put the output in `LIBRARY_AUTH_HASH` in `.env` using single quotes, and set `LIBRARY_AUTH_USER` to the group login name. Share that username and password only with your friends.
4. Put the model files required by your ComfyUI workflows under `models/` in the folders expected by those workflows.
5. From this directory, build and start the stack:

   ```bash
   docker compose build
   docker compose up -d
   docker compose logs -f web comfyui media-api media-db media-web caddy
   ```

6. Open `https://DOMAIN`, sign in with the admin credentials, and create accounts for your friends in **Admin settings**. Open `https://LIBRARY_DOMAIN` and sign in with the shared group password.

Caddy requests and renews the HTTPS certificate automatically after DNS and ports 80/443 are reachable. Avoid sharing the site over plain HTTP.

## Image library and GraphQL

Open the library at `https://LIBRARY_DOMAIN`. Create a top-level group for a series, then make child groups for issues and upload page images into each issue. The sidebar shows the hierarchy. Use the arrow buttons to order pages. Deleting a group also deletes its child groups and images that belong only to those groups; images attached to another group are kept.

The frontend talks to Apollo Server at `https://LIBRARY_DOMAIN/graphql`. The API also has a multipart upload endpoint at `/upload` and serves protected image files under `/media/{id}`. Caddy requires the shared Basic Auth login and forwards a separate private header to the API; the API rejects requests that bypass the proxy. PostgreSQL is accessible only inside the Docker network. Generated images from the generator can be downloaded and uploaded to a library group.

The database creates its tables on first startup. No SQL setup is required. If you later want per-friend access, sharing links, image import from the generator, tags, or comic-reader navigation, those can be added to the GraphQL schema and UI.

## Default model workflows

The text workflow `workflows/text2img.api.json` uses SDXL Base. The edit workflow `workflows/img2img.api.json` uses Qwen-Image-Edit-2509 with a bundled ComfyUI node for up to ten separate reference images. See [the update instructions](docs/update-multi-reference-de.md) for setup, model downloads, limits and tests. Download `sd_xl_base_1.0.safetensors` from the [official Stability AI SDXL repository](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0) and place it here:

```text
models/checkpoints/sd_xl_base_1.0.safetensors
```

Install SDXL for text generation and the Qwen files for editing, then rebuild the three updated services:

```bash
bash scripts/download-sdxl.sh
bash qwen-edit-setup/download-models.sh
docker compose --profile discord build comfyui web discord
docker compose --profile discord up -d
```

Edit mode takes one to ten references. Mention “Image 1”, “Image 2”, etc. in the prompt.
All inputs are passed separately, with a shared reference pixel budget; ten images have
less detail per image. The model card recommends 1–3 references, so quality with more
images requires testing on your GPU. Standard limits are 15 MB per image and 60 MB total.
ComfyUI validation failures now show the missing model or actual rejection reason.

To use another workflow filename, edit `WORKFLOW_TEXT` or `WORKFLOW_EDIT` in `compose.yaml` and mount that file from `./workflows`.

## Optional Discord bot

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications), add a bot, and copy its token into `.env` as `DISCORD_TOKEN`.
2. Invite it to your server with the `bot` and `applications.commands` scopes. It only needs permission to view the channel, send messages, and attach files.
3. Invite the bot to the servers where it should be used. Commands are synchronized globally and work only in installed server contexts. No server or user IDs need to be configured.
4. Set `DISCORD_BOT_SECRET` to a second random secret (for example `openssl rand -hex 32`).
5. Start the optional service:

   ```bash
   docker compose --profile discord up -d --build
   ```

The commands are `/imagine prompt:...` and `/edit prompt:... reference:... reference2:...` through `reference10:...`. Only the first reference is required. Discord uses the same blocked phrases and mode toggles as the website. To stop just the bot, run `docker compose stop discord`.

## Policy and model administration

The admin page supports:

- One blocked word or phrase per line. Matching ignores case and punctuation differences.
- Turning text generation or reference-image editing off for everyone.
- A maximum prompt length, up to 2,000 characters.
- Creating separate sign-in accounts for each friend.

Model weights and ComfyUI workflows are system-level files. Only someone with SSH access to the instance can change them. Do not expose ComfyUI's own web interface, the media API, or PostgreSQL to the public internet. Only Caddy publishes host ports.

## Backups and maintenance

Back up `.env`, `data/`, `media-files/`, `models/`, and any custom workflows. Export the database metadata with `docker compose exec -T media-db pg_dump -U image_library image_library > image-library-backup.sql`. Generated images are stored in `data/images/`; ComfyUI also keeps intermediate outputs in `comfy-output/`. These directories grow over time, so periodically remove old files after taking any backups you want to keep. Update the project deliberately and test the model workflow and image upload after updates:

```bash
docker compose pull caddy
docker compose build --pull
docker compose up -d
```

## Layout

```text
compose.yaml             web, local model worker, HTTPS proxy, optional Discord bot
web/app.py               account portal, policy checks, image gallery, internal bot API
discord/bot.py           allow-listed Discord slash commands
media-api/src/index.ts   Apollo Server, PostgreSQL, image upload and collection API
media-web/src/           React + TypeScript image-management website
media-files/             uploaded image binaries (created on first upload)
postgres_data             database volume for metadata and collection relations
workflows/               ComfyUI API workflow files
models/                  local model weights (not included)
data/                    SQLite accounts, rules, and generated images
```
