from django.urls import path
from . import selection_template_views as v

urlpatterns = [
    path('companies/<slug:slug>/selection-templates/<int:template_pk>/load/', v.choose_project, name='selection_template_choose_project'),
    path('companies/<slug:slug>/selection-templates/', v.library, name='selection_template_library'),
    path('companies/<slug:slug>/selection-templates/new/', v.create, name='selection_template_create'),
    path('companies/<slug:slug>/selection-templates/<int:template_pk>/', v.detail, name='selection_template_detail'),
    path('companies/<slug:slug>/selection-templates/<int:template_pk>/edit/', v.metadata, name='selection_template_edit'),
    path('companies/<slug:slug>/selection-templates/<int:template_pk>/copy/', v.copy, name='selection_template_copy'),
    path('companies/<slug:slug>/selection-templates/<int:template_pk>/delete/', v.delete, name='selection_template_delete'),
    path('companies/<slug:slug>/selection-templates/<int:template_pk>/rows/<int:row_pk>/', v.row_edit, name='selection_template_row'),
    path('companies/<slug:slug>/selection-templates/<int:template_pk>/rows/<int:row_pk>/choices/new/', v.option_edit, name='selection_template_option_create'),
    path('companies/<slug:slug>/selection-templates/<int:template_pk>/rows/<int:row_pk>/choices/<int:option_pk>/', v.option_edit, name='selection_template_option_edit'),
    path('companies/<slug:slug>/selection-templates/<int:template_pk>/rows/<int:row_pk>/choices/<int:option_pk>/delete/', v.option_delete, name='selection_template_option_delete'),
    path('companies/<slug:slug>/selection-templates/<int:template_pk>/rows/<int:row_pk>/choices/<int:option_pk>/<str:kind>/', v.media, name='selection_template_media'),
    path('projects/<int:pk>/selections/save-template/', v.save_project, name='selection_template_save'),
    path('projects/<int:pk>/selections/load-template/<int:template_pk>/', v.load, name='selection_template_load'),
]
