"""Database router for the shared satellite-blog DB.

The ``BlogPost`` model lives in a separate Neon database (``blog``) that the
external blog sites also read. Everything else stays on ``default``. The blog DB
holds ONLY the BlogPost table (no cross-DB FKs), so the satellite sites can read
it standalone.
"""


class BlogRouter:
    blog_models = {"blogpost"}

    def db_for_read(self, model, **hints):
        if model._meta.model_name in self.blog_models:
            return "blog"
        return None

    def db_for_write(self, model, **hints):
        if model._meta.model_name in self.blog_models:
            return "blog"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # BlogPost migrates only on the blog DB; the blog DB holds nothing else.
        if model_name in self.blog_models:
            return db == "blog"
        if db == "blog":
            return False
        return None
