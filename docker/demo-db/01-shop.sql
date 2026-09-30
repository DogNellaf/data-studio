-- Демонстрационная база «магазин» для DataStudio.
-- Колонки updated_at / created_at нужны инкрементальным и
-- дифференциальным копиям, чтобы находить изменившиеся строки.

CREATE TABLE customers (
    id          serial PRIMARY KEY,
    name        text        NOT NULL,
    email       text        NOT NULL UNIQUE,
    city        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE products (
    id          serial PRIMARY KEY,
    title       text           NOT NULL,
    price       numeric(10, 2) NOT NULL CHECK (price >= 0),
    stock       integer        NOT NULL DEFAULT 0,
    updated_at  timestamptz    NOT NULL DEFAULT now()
);

CREATE TABLE orders (
    id           serial PRIMARY KEY,
    customer_id  integer     NOT NULL REFERENCES customers (id),
    status       text        NOT NULL DEFAULT 'new',
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE order_items (
    order_id    integer        NOT NULL REFERENCES orders (id),
    product_id  integer        NOT NULL REFERENCES products (id),
    quantity    integer        NOT NULL CHECK (quantity > 0),
    price       numeric(10, 2) NOT NULL,
    created_at  timestamptz    NOT NULL DEFAULT now(),
    PRIMARY KEY (order_id, product_id)
);

-- Справочник без колонок времени: попадает только в полную копию.
CREATE TABLE settings (
    key    text PRIMARY KEY,
    value  text NOT NULL
);

-- Поддерживает updated_at в актуальном состоянии при любом UPDATE.
CREATE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER customers_touch BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER products_touch BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER orders_touch BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

INSERT INTO settings (key, value) VALUES
    ('currency', 'RUB'),
    ('shop_name', 'Demo Shop');

INSERT INTO customers (name, email, city)
SELECT
    'Покупатель ' || n,
    'customer' || n || '@example.com',
    (ARRAY['Москва', 'Санкт-Петербург', 'Казань', 'Новосибирск', 'Екатеринбург'])[1 + n % 5]
FROM generate_series(1, 500) AS n;

INSERT INTO products (title, price, stock)
SELECT
    'Товар №' || n,
    round((100 + random() * 9900)::numeric, 2),
    (random() * 200)::int
FROM generate_series(1, 200) AS n;

INSERT INTO orders (customer_id, status, created_at, updated_at)
SELECT
    1 + (n % 500),
    (ARRAY['new', 'paid', 'shipped', 'delivered'])[1 + n % 4],
    now() - (n || ' minutes')::interval,
    now() - (n || ' minutes')::interval
FROM generate_series(1, 2000) AS n;

INSERT INTO order_items (order_id, product_id, quantity, price)
SELECT o.id, p.id, 1 + (o.id + p.id) % 3, p.price
FROM orders o
JOIN products p ON p.id IN (1 + o.id % 200, 1 + (o.id * 7) % 200)
ON CONFLICT DO NOTHING;
