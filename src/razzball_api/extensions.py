from flask_sqlalchemy import SQLAlchemy

# Bound to an application in create_app(). The default engine is the baseball
# database; "basketball" and "football" are configured as binds.
db = SQLAlchemy()
