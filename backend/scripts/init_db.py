import app.database.models  # noqa: F401
from app.database.connection import Base, engine


def init_db():
    print("Creating database tables...")
    try:
        with engine.connect():
            print(f"Connected to database: {engine.url}")

        Base.metadata.create_all(bind=engine)
        print("All tables created successfully!")
    except Exception as e:
        print(f"Error creating database tables: {e}")


if __name__ == "__main__":
    init_db()
