# Supabase/Render production cutover

1. In Supabase SQL Editor run the complete `supabase_schema.sql`. Confirm the `uploads` bucket is public and set Storage policies as shown there.
2. Import existing products into `products`, mapping the image field to `image_url` as a complete `https://<project>.supabase.co/storage/v1/object/public/uploads/...` URL. Do not import `/uploads/...` paths.
3. In Render set `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_BUCKET=uploads`, `UPLOAD_MODE=supabase`, a random `SECRET_KEY`, `FLASK_ENV=production`, `ADMIN_EMAILS`, and mail variables. Never expose the service role key to browser JavaScript.
4. Delete `data/site.json`, `site.json` (if present), all `data/uploads/`, `/static/uploads/`, and any old committed upload images. Keep only source/brand assets that are intentionally part of the static site; product/category uploads belong in Supabase Storage.
5. Review the admin panel: site setting updates call `POST /api/admin/site`; checkout reads `GET /api/site`. Both are live Supabase reads/writes.
6. Commit on `arena/01a0782f-jaurastore`, push that branch, open a PR, and merge it into `main`. Render will deploy the merge commit. Run `/healthz`, `/api/site`, an admin upload, password reset, and a checkout smoke test after deploy.
