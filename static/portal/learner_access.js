// learner_access.js

document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('learner-access-form');
    const hiddenInput = document.querySelector('input[name="access_data"]');
    const checkboxes = document.querySelectorAll('.access-checkbox');

    function updateChildren(parent) {
        const li = parent.closest('li');
        if (!li) return;
        const childChecks = li.querySelectorAll('.access-checkbox');
        childChecks.forEach(cb => {
            if (cb === parent) return;
            if (!parent.checked) { // parent explicitly denied
                cb.disabled = true;
                cb.checked = true; // show inherited deny as checked
                cb.dataset.inherited = 'true';
            } else { // parent allowed
                if (cb.dataset.inherited === 'true') {
                    // Restore explicit state
                    cb.disabled = false;
                    const explicit = cb.dataset.explicit === 'true';
                    cb.checked = !explicit; // allowed if not explicitly denied
                    delete cb.dataset.inherited;
                }
            }
        });
    }

    checkboxes.forEach(cb => {
        cb.addEventListener('change', function () {
            updateChildren(cb);
        });
    });

    form.addEventListener('submit', function (e) {
        const denied = [];
        checkboxes.forEach(cb => {
            // explicit deny: unchecked and not disabled (i.e., not inherited)
            if (!cb.checked && !cb.disabled) {
                denied.push({ type: cb.dataset.type, id: parseInt(cb.dataset.id, 10) });
            }
        });
        hiddenInput.value = JSON.stringify(denied);
    });
});
