document.querySelectorAll('[data-project-access-form]').forEach((form) => {
  const manager = form.querySelector('[name="is_project_manager"]');
  const permissions = ['can_manage', 'can_invite_clients'].map((name) => form.elements.namedItem(name));
  if (!manager || permissions.some((field) => !field)) return;
  const update = () => {
    permissions.forEach((field) => {
      if (manager.checked) field.checked = true;
      field.disabled = manager.checked;
    });
  };
  manager.addEventListener('change', update);
  update();
});
