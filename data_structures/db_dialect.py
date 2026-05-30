from abc import ABC, abstractmethod
from typing import Optional


class DBDialect(ABC):
    """Database dialect abstract base class"""
    @property
    @abstractmethod
    def name(self):
        """Database name"""
        pass
    
    @abstractmethod
    def get_create_database_sql(self, db_name: str) -> Optional[str]:
        """Get SQL statement for creating a database"""
        pass
    
    @abstractmethod
    def get_use_database_sql(self, db_name: str) -> str:
        """Get SQL statement for using a database"""
        pass
    
    @abstractmethod
    def get_drop_database_sql(self, db_name: str) -> str:
        """Get SQL statement for dropping a database"""
        pass
    
    @abstractmethod
    def get_column_definition(self, col_name: str, data_type: str, nullable: bool, is_primary_key: bool = False) -> str:
        """Get SQL statement for column definition"""
        pass
    
    @abstractmethod
    def get_primary_key_constraint(self, primary_key: str) -> str:
        """Get SQL statement for primary key constraint"""
        pass
    
    @abstractmethod
    def get_datetime_literal(self, year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> str:
        """Get SQL representation of datetime literal"""
        pass
    
    @abstractmethod
    def get_function_name(self, function_name: str) -> str:
        """Get dialect-specific function name"""
        pass
    
    @abstractmethod
    def get_literal_representation(self, value: str, data_type: str) -> str:
        """Get dialect-specific representation of literal"""
        pass
    
    @abstractmethod
    def get_create_index_sql(self, table_name: str, index_name: str, columns: list, is_unique: bool = False) -> str:
        """Get SQL statement for creating an index"""
        pass

    def supports_cte(self) -> bool:
        return True

    def supports_function(self, name: str) -> bool:
        return True

    def optimizer_family(self) -> str:
        return self.name.lower()

    def supports_prefix_index_on_varchar(self) -> bool:
        return True

    def supports_prefix_index_on_text(self) -> bool:
        return True

    def supports_explain_format_traditional(self) -> bool:
        return True

    def supports_json_type(self) -> bool:
        return True

    def unchanged_plan_verify_prob(self) -> float:
        return 0.15

    def quote_identifier(self, name: str) -> str:
        return name

    def quote_qualified_name(self, *parts: str) -> str:
        return ".".join(self.quote_identifier(part) for part in parts if part)

    def get_insert_keyword(self, ignore_duplicates: bool = False) -> str:
        return "INSERT"

    def get_analyze_table_sql(self, table_name: str, schema_name: Optional[str] = None) -> str:
        return f"ANALYZE TABLE {self.quote_qualified_name(schema_name, table_name)}"

    def get_set_schema_sql(self, schema_name: str) -> Optional[str]:
        return None

    def get_current_schema_sql(self) -> Optional[str]:
        return None

class MySQLDialect(DBDialect):
    """MySQL database dialect implementation"""
    @property
    def name(self):
        return "MYSQL"
    
    def get_create_database_sql(self, db_name: str) -> Optional[str]:
        return (
            f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
    
    def get_use_database_sql(self, db_name: str) -> str:
        return f"USE {db_name};"
    
    def get_drop_database_sql(self, db_name: str) -> str:
        return f"DROP DATABASE IF EXISTS {db_name};"
    
    def get_column_definition(self, col_name: str, data_type: str, nullable: bool, is_primary_key: bool = False) -> str:
        nullable_str = "NULL" if nullable else "NOT NULL"
        if is_primary_key and "INT" in data_type:
            return f"{col_name} {data_type} {nullable_str} AUTO_INCREMENT"
        return f"{col_name} {data_type} {nullable_str}"
    
    def get_primary_key_constraint(self, primary_key: str) -> str:
        return f"PRIMARY KEY ({primary_key})"
    
    def get_datetime_literal(self, year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> str:
        # Check if only date part is needed (all time parameters are default 0)
        if hour == 0 and minute == 0 and second == 0:
            return f"'{year:04d}-{month:02d}-{day:02d}'"
        else:
            return f"'{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}'"
    
    def get_function_name(self, function_name: str) -> str:
        # MySQL function name mappings
        function_mapping = {
            "TO_CHAR": "DATE_FORMAT",
            "VARIANCE_POP": "VAR_POP",
            "VARIANCE_SAMP": "VAR_SAMP"
        }
        return function_mapping.get(function_name, function_name)
    
    def get_literal_representation(self, value: str, data_type: str) -> str:
        # Ensure data_type is a string and not None
        if data_type is None:
            data_type = 'UNKNOWN'
        
        # Special handling for NULL values
        if value is None:
            return 'NULL'
        
        # Ensure value is a string
        value_str = str(value)
        
        # Special handling for datetime types, ensure quotes are always added
        if data_type.upper() in ['DATE', 'DATETIME', 'TIMESTAMP']:
            # Check if value is already enclosed in single or double quotes
            if (value_str.startswith("'") and value_str.endswith("'") or 
                value_str.startswith('"') and value_str.endswith('"')):
                # If already quoted, return original value (no additional escaping)
                return value_str
            # Filter non-ASCII characters to ensure only valid UTF-8 characters are included
            safe_value = ''.join(char for char in value_str if ord(char) < 128)
            # Escape single quotes and add quotes
            escaped_value = safe_value.replace("'", "''")
            return f"'{escaped_value}'"
        elif data_type.upper() in ['VARCHAR', 'VARCHAR(255)', 'STRING']:
            # Ensure value is a string
            # Check if value is already enclosed in single or double quotes
            if (value_str.startswith("'") and value_str.endswith("'") or 
                value_str.startswith('"') and value_str.endswith('"')):
                # If already quoted, return original value (no additional escaping)
                return value_str
            # Filter non-ASCII characters to ensure only valid UTF-8 characters are included
            # Use ASCII encoding, ignore non-ASCII characters
            safe_value = ''.join(char for char in value_str if ord(char) < 128)
            # Escape single quotes and add quotes
            escaped_value = safe_value.replace("'", "''")
            return f"'{escaped_value}'"
        elif data_type.upper() in ['BOOLEAN', 'BOOL']:
            # MySQL uses TRUE/FALSE strings to represent boolean values
            return f"'{str(value).lower()}'"
        elif data_type.upper() == 'JSON':
            # Special handling for JSON type, ensure proper quoting and encoding
            value_str = str(value)
            # Check if value is already enclosed in single quotes
            if value_str.startswith("'") and value_str.endswith("'"):
                return value_str
            # Filter non-ASCII characters to ensure only valid UTF-8 characters are included
            safe_value = ''.join(char for char in value_str if ord(char) < 128)
            # Ensure JSON string is enclosed in single quotes and internal single quotes are escaped
            escaped_value = safe_value.replace("'", "''")
            return f"'{escaped_value}'"
        elif data_type.upper() in ['BLOB', 'TINYBLOB', 'MEDIUMBLOB', 'LONGBLOB']:
            # Special handling for binary types to meet utf8mb4 requirements
            # Check if value is already in hexadecimal format (X'...')
            if value_str.startswith("X'") and value_str.endswith("'"):
                # Extract hexadecimal part
                hex_part = value_str[2:-1]
                # Ensure hexadecimal data length is even (complete bytes)
                if len(hex_part) % 2 != 0:
                    hex_part = '0' + hex_part
                # Validate hexadecimal characters
                if all(c in '0123456789ABCDEFabcdef' for c in hex_part):
                    # For utf8mb4, need to ensure binary data is a valid UTF-8 encoded sequence
                    # Since we cannot directly verify UTF-8 validity of binary data in string representation
                    # We choose to use MySQL's CONVERT function to explicitly convert it to utf8mb4
                    return f"CONVERT({value_str} USING utf8mb4)"
            # If not in standard format, return empty binary value
            return "X''"
        # For other data types, ensure returned string is valid UTF-8
        value_str = str(value)
        # Check if it's a string type value
        if isinstance(value, str):
            # Filter non-ASCII characters to ensure only valid UTF-8 characters are included
            safe_value = ''.join(char for char in value_str if ord(char) < 128)
            return safe_value
        return value_str
    
    def get_create_index_sql(self, table_name: str, index_name: str, columns: list, is_unique: bool = False) -> str:
        """Get SQL statement for creating MySQL index"""
        unique_clause = "UNIQUE" if is_unique else ""
        columns_str = ", ".join(columns)
        return f"CREATE {unique_clause} INDEX {index_name} ON {table_name} ({columns_str});"
    
    def supports_cte(self) -> bool:
        """MySQL 8.0+ 支持 CTE"""
        return True

    def supports_function(self, name: str) -> bool:
        """该方言是否可安全使用此函数名"""
        return True  # MySQL 默认全部支持

    def optimizer_family(self) -> str:
        return 'mysql'

    def supports_prefix_index_on_varchar(self) -> bool:
        return True

    def supports_prefix_index_on_text(self) -> bool:
        return True

    def supports_explain_format_traditional(self) -> bool:
        return True

    def supports_json_type(self) -> bool:
        return True

    def quote_identifier(self, name: str) -> str:
        return f"`{name}`"

    def get_insert_keyword(self, ignore_duplicates: bool = False) -> str:
        return "INSERT IGNORE" if ignore_duplicates else "INSERT"


class PostgreSQLDialect(DBDialect):
    """PostgreSQL database dialect implementation"""
    @property
    def name(self):
        return "POSTGRESQL"
    
    def get_create_database_sql(self, db_name: str) -> str:
        # PostgreSQL does not support IF NOT EXISTS clause
        return f"CREATE DATABASE {db_name};"
    
    def get_use_database_sql(self, db_name: str) -> str:
        # PostgreSQL does not have USE statement, database connection is specified through connection parameters
        return ""
    
    def get_drop_database_sql(self, db_name: str) -> str:
        # PostgreSQL needs to disconnect all connections before dropping database
        terminate_sql = f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{db_name}';"
        drop_sql = f"DROP DATABASE IF EXISTS {db_name};"
        return f"{terminate_sql}\n{drop_sql}"
    
    def get_column_definition(self, col_name: str, data_type: str, nullable: bool, is_primary_key: bool = False) -> str:
        nullable_str = "NULL" if nullable else "NOT NULL"
        if is_primary_key and "INT" in data_type:
            # PostgreSQL uses SERIAL or GENERATED ALWAYS AS IDENTITY
            return f"{col_name} SERIAL PRIMARY KEY"
        # PostgreSQL uses TIMESTAMP instead of DATETIME
        if data_type == "DATETIME":
            data_type = "TIMESTAMP"
        return f"{col_name} {data_type} {nullable_str}"
    
    def get_primary_key_constraint(self, primary_key: str) -> str:
        # For SERIAL type primary keys, no need to add PRIMARY KEY constraint separately
        return f"PRIMARY KEY ({primary_key})"
    
    def get_datetime_literal(self, year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> str:
        # Check if only date part is needed (all time parameters are default 0)
        if hour == 0 and minute == 0 and second == 0:
            return f"'{year:04d}-{month:02d}-{day:02d}'"
        else:
            # PostgreSQL datetime format is compatible with MySQL
            return f"'{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}'"
    
    def get_function_name(self, function_name: str) -> str:
        # PostgreSQL function name mapping
        function_mapping = {
            "DATE_FORMAT": "TO_CHAR"
        }
        return function_mapping.get(function_name, function_name)
    
    def get_literal_representation(self, value: str, data_type: str) -> str:
        # Ensure data_type is a string and not None
        if data_type is None:
            data_type = 'UNKNOWN'
        
        # Special handling for NULL values
        if value is None:
            return 'NULL'
        
        # Ensure value is a string
        value_str = str(value)
        # Check if value is already enclosed in single or double quotes
        is_quoted = (value_str.startswith("'") and value_str.endswith("'") or 
                    value_str.startswith('"') and value_str.endswith('"'))
        
        # For string, date, and timestamp types, add single quotes
        if data_type.upper() in ['VARCHAR', 'VARCHAR(255)', 'TEXT', 'DATE', 'TIMESTAMP', 'STRING', 'DATETIME', 'TIME']:
            if is_quoted:
                # If already quoted, return original value (no additional escaping)
                return value_str
            # Otherwise escape single quotes and add quotes
            escaped_value = value_str.replace("'", "''")
            # Ensure to return string with single quotes
            return f"'{escaped_value}'"
        elif data_type.upper() in ['BOOLEAN', 'BOOL']:
            # PostgreSQL uses TRUE/FALSE keywords to represent boolean values
            return str(value).upper()
        # For other types (such as numeric types), directly return string representation
        return str(value)
    
    def get_create_index_sql(self, table_name: str, index_name: str, columns: list, is_unique: bool = False) -> str:
        """Get SQL statement for creating PostgreSQL index"""
        unique_clause = "UNIQUE" if is_unique else ""
        columns_str = ", ".join(columns)
        return f"CREATE {unique_clause} INDEX {index_name} ON {table_name} ({columns_str});"

    def quote_identifier(self, name: str) -> str:
        return f'"{name}"'


class MonetDBDialect(PostgreSQLDialect):
    """MonetDB database dialect implementation"""
    @property
    def name(self):
        return "MONETDB"

    def get_create_database_sql(self, db_name: str) -> Optional[str]:
        # MonetDB databases are provisioned outside regular SQL sessions.
        return None

    def get_use_database_sql(self, db_name: str) -> str:
        # Database selection happens during connection establishment.
        return ""

    def get_drop_database_sql(self, db_name: str) -> str:
        return ""

    def get_column_definition(self, col_name: str, data_type: str, nullable: bool, is_primary_key: bool = False) -> str:
        nullable_str = "NULL" if nullable else "NOT NULL"
        if data_type == "DATETIME":
            data_type = "TIMESTAMP"
        if is_primary_key and "INT" in data_type.upper():
            return f'{self.quote_identifier(col_name)} {data_type} {nullable_str} AUTO_INCREMENT'
        return f'{self.quote_identifier(col_name)} {data_type} {nullable_str}'

    def get_primary_key_constraint(self, primary_key: str) -> str:
        return f"PRIMARY KEY ({self.quote_identifier(primary_key)})"

    def get_function_name(self, function_name: str) -> str:
        function_mapping = {
            "DATE_FORMAT": "TO_CHAR",
            "VARIANCE_POP": "VAR_POP",
            "VARIANCE_SAMP": "VAR_SAMP",
        }
        return function_mapping.get(function_name, function_name)

    def get_literal_representation(self, value: str, data_type: str) -> str:
        if data_type == "DATETIME":
            data_type = "TIMESTAMP"
        return super().get_literal_representation(value, data_type)

    def supports_function(self, name: str) -> bool:
        # Avoid MonetDB math-domain / overflow false positives from generated queries.
        unsupported = {'ROUND', 'REVERSE', 'LOG', 'SQRT', 'EXP'}
        return name not in unsupported

    def optimizer_family(self) -> str:
        return "monetdb"

    def supports_prefix_index_on_varchar(self) -> bool:
        return False

    def supports_prefix_index_on_text(self) -> bool:
        return False

    def supports_explain_format_traditional(self) -> bool:
        return False

    def unchanged_plan_verify_prob(self) -> float:
        return 1.0

    def get_analyze_table_sql(self, table_name: str, schema_name: Optional[str] = None) -> str:
        return f"ANALYZE {self.quote_qualified_name(schema_name, table_name)}"

    def get_set_schema_sql(self, schema_name: str) -> Optional[str]:
        return f"SET SCHEMA {self.quote_identifier(schema_name)}"

    def get_current_schema_sql(self) -> Optional[str]:
        return "SELECT CURRENT_SCHEMA"


class MariaDBDialect(MySQLDialect):
    """MariaDB database dialect implementation"""
    @property
    def name(self):
        return "MARIADB"
    
    def get_column_definition(self, col_name: str, data_type: str, nullable: bool, is_primary_key: bool = False) -> str:
        # MariaDB treats JSON as an alias for LONGTEXT and requires special handling
        if data_type.upper() == "JSON":
            data_type = "LONGTEXT"
        # For GEOMETRY type, keep as is but note MariaDB implementation differences
        return super().get_column_definition(col_name, data_type, nullable, is_primary_key)
    
    def get_function_name(self, function_name: str) -> str:
        # MariaDB-specific function name mappings
        function_mapping = {
            "TO_CHAR": "DATE_FORMAT",
            "VARIANCE_POP": "VAR_POP",
            "VARIANCE_SAMP": "VAR_SAMP"
            # Keep the same function mappings as MySQL, but note potential implementation differences in MariaDB
        }
        return function_mapping.get(function_name, function_name)
    
    def get_literal_representation(self, value: str, data_type: str) -> str:
        # Ensure data_type is a string and not None
        if data_type is None:
            data_type = 'UNKNOWN'
        
        # MariaDB treats JSON as LONGTEXT alias, process as LONGTEXT
        if data_type.upper() == "JSON":
            data_type = "LONGTEXT"
        
        # For BOOLEAN type, MariaDB internally uses TINYINT(1)
        if data_type.upper() in ['BOOLEAN', 'BOOL']:
            # Ensure value is a string
            value_str = str(value).lower() if value is not None else 'null'
            if value_str in ['true', 'false']:
                return '1' if value_str == 'true' else '0'
        
        # For spatial data types, maintain special handling but note MariaDB differences
        if data_type.upper() in ['GEOMETRY', 'POINT', 'LINESTRING', 'POLYGON']:
            # MariaDB's spatial data handling may have subtle differences from MySQL, return NULL to avoid issues
            return "NULL"
        
        # Call parent class method to handle other data types
        return super().get_literal_representation(value, data_type)
    
    def supports_native_json(self) -> bool:
        """MariaDB does not support native JSON type and treats it as LONGTEXT alias"""
        return False

    def supports_json_type(self) -> bool:
        return False
    
    def supports_math_equivalence_transformations(self) -> bool:
        """MariaDB's mathematical equivalence transformations might have issues, especially with MIN/MAX and negative value conversions"""
        return False
    
    def supports_cte(self) -> bool:
        return True  # MariaDB 10.2+ 支持

    def supports_function(self, name: str) -> bool:
        # 这些函数在 MariaDB 上行为不稳定，产生误报概率更高
        _NOISY = {'LOG', 'EXP', 'SIN', 'COS', 'TAN'}
        return name not in _NOISY

    def optimizer_family(self) -> str:
        return 'mariadb'

class TiDBDialect(MySQLDialect):
    """TiDB database dialect implementation"""
    @property
    def name(self):
        return "TIDB"
    
    def supports_subqueries_in_join_condition(self) -> bool:
        """TiDB does not support subqueries in ON conditions"""
        return False
        
    def supports_share_lock_mode(self) -> bool:
        """TiDB only has a no-op implementation for LOCK IN SHARE MODE, should be avoided"""
        return False

    def optimizer_family(self) -> str:
        return 'tidb'

    def supports_prefix_index_on_varchar(self) -> bool:
        return False

    def supports_prefix_index_on_text(self) -> bool:
        return False

    def supports_explain_format_traditional(self) -> bool:
        return False


class OceanBaseDialect(MySQLDialect):
    """OceanBase database dialect implementation"""
    @property
    def name(self):
        return "OCEANBASE"

    def get_create_database_sql(self, db_name: str) -> Optional[str]:
        return (
            f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
        )

    def optimizer_family(self) -> str:
        return 'oceanbase'

    def supports_cte(self) -> bool:
        return False

    def supports_prefix_index_on_varchar(self) -> bool:
        return False

    def supports_prefix_index_on_text(self) -> bool:
        return False

    def supports_explain_format_traditional(self) -> bool:
        return False


class PerconaDialect(MySQLDialect):
    """Percona database dialect implementation"""
    @property
    def name(self):
        return "PERCONA"
    
    def supports_cte(self) -> bool:
        # Percona 5.7 不支持 CTE；Percona 8.0+ 支持
        # 保守起见降权而非完全禁用，通过 _choose_shape 权重控制
        return True

    def optimizer_family(self) -> str:
        return 'percona'


class PolarDBDialect(MySQLDialect):
    """PolarDB database dialect implementation"""
    @property
    def name(self):
        return "POLARDB"

    def get_create_database_sql(self, db_name: str) -> Optional[str]:
        return None
    
    def get_column_definition(self, col_name: str, data_type: str, nullable: bool, is_primary_key: bool = False) -> str:
        # For foreign key columns, don't add foreign key constraint related logic
        # Only handle basic column definition and primary key auto-increment
        nullable_str = "NULL" if nullable else "NOT NULL"
        if is_primary_key and "INT" in data_type:
            return f"{col_name} {data_type} {nullable_str} AUTO_INCREMENT"
        return f"{col_name} {data_type} {nullable_str}"

    def get_primary_key_constraint(self, primary_key: str) -> str:
        # Process primary key constraint normally
        return f"PRIMARY KEY ({primary_key})"
    
    def get_function_name(self, function_name: str) -> str:
        # Unsupported aggregate function mapping for PolarDB-X
        # Map unsupported standard deviation and variance functions to AVG function as alternatives
        unsupported_functions = {
            "STD": "AVG",
            "STDDEV": "AVG",
            "STDDEV_POP": "AVG",
            "STDDEV_SAMP": "AVG",
            "VARIANCE": "AVG",
            "VARIANCE_POP": "AVG",
            "VARIANCE_SAMP": "AVG",
            "VAR_POP": "AVG",
            "VAR_SAMP": "AVG"
        }
        
        # First check if it's an unsupported function
        if function_name in unsupported_functions:
            return unsupported_functions[function_name]
        
        # Then apply MySQL function mappings
        function_mapping = {
            "TO_CHAR": "DATE_FORMAT"
        }
        return function_mapping.get(function_name, function_name)

    def optimizer_family(self) -> str:
        return 'polardb'

    def supports_cte(self) -> bool:
        return False
    
    def unchanged_plan_verify_prob(self) -> float:
        return 1.0

    def supports_prefix_index_on_varchar(self) -> bool:
        return False

    def supports_prefix_index_on_text(self) -> bool:
        return False

    def supports_explain_format_traditional(self) -> bool:
        return False

    def supports_json_type(self) -> bool:
        return False
    
    def supports_foreign_keys(self) -> bool:
        """PolarDB does not support foreign keys"""
        return False
    
    def supports_subqueries_in_join(self) -> bool:
        """PolarDB does not support subqueries in JOIN conditions"""
        return False
    
    def supports_share_lock_mode(self) -> bool:
        """PolarDB supports LOCK IN SHARE MODE, but needs adaptation for other modes like FOR KEY SHARE"""
        return True
    
    def supports_except_operator(self) -> bool:
        """Whether PolarDB-X supports EXCEPT operator"""
        # PolarDB-X as MySQL compatible database, doesn't directly support EXCEPT operator by default
        return False
    
    def supports_intersect_operator(self) -> bool:
        """Whether PolarDB-X supports INTERSECT operator"""
        # PolarDB-X as MySQL compatible database, doesn't directly support INTERSECT operator by default
        return False


class DBDialectFactory:
    """Database dialect factory class"""
    _dialects = {
        "MYSQL": MySQLDialect,
        "POSTGRESQL": PostgreSQLDialect,
        "MONETDB": MonetDBDialect,
        "MARIADB": MariaDBDialect,
        "TIDB": TiDBDialect,
        "OCEANBASE": OceanBaseDialect,
        "PERCONA": PerconaDialect,
        "POLARDB": PolarDBDialect
    }
    
    _current_dialect = None
    
    @classmethod
    def get_dialect(cls, dialect_name: str) -> DBDialect:
        """Get database dialect instance with specified name"""
        dialect_name = dialect_name.upper()
        if dialect_name not in cls._dialects:
            raise ValueError(f"Unsupported database dialect: {dialect_name}")
        return cls._dialects[dialect_name]()
    
    @classmethod
    def set_current_dialect(cls, dialect_name: str) -> None:
        """Set currently used database dialect"""
        cls._current_dialect = cls.get_dialect(dialect_name)
    
    @classmethod
    def get_current_dialect(cls) -> DBDialect:
        """Get currently used database dialect"""
        if cls._current_dialect is None:
            # Use MySQL dialect by default
            cls.set_current_dialect("MYSQL")
        return cls._current_dialect


# Set default dialect to MySQL
def get_current_dialect() -> DBDialect:
    """Convenience function to get the currently used database dialect"""
    return DBDialectFactory.get_current_dialect()

def set_current_dialect(dialect_name: str) -> None:
    """Convenience function to set the currently used database dialect"""
    DBDialectFactory.set_current_dialect(dialect_name)

# For backward compatibility, add these function aliases
def set_dialect(dialect_name: str) -> None:
    """Set the currently used database dialect (alias function for backward compatibility)"""
    DBDialectFactory.set_current_dialect(dialect_name)

def get_dialect_config() -> DBDialect:
    """Get the currently used database dialect configuration (alias function for backward compatibility)"""
    return DBDialectFactory.get_current_dialect()
