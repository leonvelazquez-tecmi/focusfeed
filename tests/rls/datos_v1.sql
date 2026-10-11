-- Datos falsos sobre el esquema v1: un dueño con feeds, items con estado y perfil.
INSERT INTO public.feeds (title, feed_url, feed_type) VALUES
  ('Canal IA', 'https://ejemplo.com/ia', 'rss'),
  ('Canal Cine', 'https://ejemplo.com/cine', 'rss');
INSERT INTO public.items (feed_id, guid, title, url, content_type, status, user_rating, relevance_score)
SELECT f.id, f.title || '-' || g, 'Item ' || g, 'https://ejemplo.com/' || g, 'article',
       CASE WHEN g % 3 = 0 THEN 'reading' WHEN g % 3 = 1 THEN 'archived' ELSE 'inbox' END,
       CASE WHEN g % 4 = 0 THEN 'like' ELSE 'none' END,
       CASE WHEN g % 2 = 0 THEN 80 ELSE NULL END
  FROM public.feeds f, generate_series(1, 6) g;
