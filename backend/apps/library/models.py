from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel


class BookCopyStatus(models.TextChoices):
    AVAILABLE = 'AVAILABLE', _('Available on Shelf')
    ISSUED = 'ISSUED', _('Issued')
    LOST = 'LOST', _('Lost')
    MAINTENANCE = 'MAINTENANCE', _('Under Binding / Maintenance')


class BookCatalog(TimeStampedModel):
    title = models.CharField(max_length=200)
    author = models.CharField(max_length=150)
    isbn = models.CharField(max_length=20, blank=True, default='')
    publisher = models.CharField(max_length=150, blank=True, default='')
    edition = models.CharField(max_length=50, blank=True, default='')
    category = models.CharField(max_length=100, blank=True, default='')

    class Meta:
        db_table = 'library_catalog'

    def __str__(self):
        return f"{self.title} by {self.author}"


class BookCopy(TimeStampedModel):
    catalog = models.ForeignKey(BookCatalog, on_delete=models.CASCADE, related_name='copies')
    accession_number = models.CharField(max_length=50, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=BookCopyStatus.choices, default=BookCopyStatus.AVAILABLE)
    shelf_location = models.CharField(max_length=50, blank=True, default='')

    class Meta:
        db_table = 'library_book_copies'

    def __str__(self):
        return f"Acc #{self.accession_number} ({self.catalog.title})"


class BookIssueRecord(TimeStampedModel):
    book_copy = models.ForeignKey(BookCopy, on_delete=models.PROTECT, related_name='issue_history')
    borrower = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='borrowed_books')
    issue_date = models.DateField(auto_now_add=True)
    due_date = models.DateField()
    return_date = models.DateField(null=True, blank=True)
    fine_amount = models.DecimalField(max_digits=8, decimal_places=2, default=0.00)
    fine_paid = models.BooleanField(default=False)

    class Meta:
        db_table = 'library_issue_records'

    def __str__(self):
        return f"{self.book_copy.accession_number} to {self.borrower.username}"
