"""Cliente de Supabase para auth y storage."""
from supabase import create_client, Client
from app.core.config import settings


def get_supabase_admin() -> Client:
    """Cliente con service_role key (operaciones admin)."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


def get_supabase_anon() -> Client:
    """Cliente con anon key (operaciones públicas)."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)


supabase_admin = get_supabase_admin()
supabase_anon = get_supabase_anon()
