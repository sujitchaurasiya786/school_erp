"""
Configuration package for School ERP backend.
"""
import pymysql

# Hook PyMySQL as MySQLdb for Django MySQL backend on Windows
pymysql.install_as_MySQLdb()
