// learner_access.js

document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('learner-access-form');
    const hiddenInput = document.querySelector('input[name="access_data"]');
    const checkboxes = document.querySelectorAll('.access-checkbox');
    const searchInput = document.getElementById('access-search');
    const filterRadios = document.querySelectorAll('input[name="accessFilter"]');
    const btnExpandAll = document.getElementById('btn-expand-all');
    const btnCollapseAll = document.getElementById('btn-collapse-all');
    
    let isDirty = false;

    const BADGE_ALLOWED = '<span class="text-success small fw-semibold"><i class="bi bi-check-circle"></i> Allowed</span>';
    const BADGE_RESTRICTED = '<span class="text-danger small fw-semibold"><i class="bi bi-x-circle"></i> Restricted</span>';
    const BADGE_INHERITED = '<span class="text-secondary small fw-semibold"><i class="bi bi-lock-fill"></i> Inherited</span>';

    function setDirty() {
        if (!isDirty) {
            isDirty = true;
            document.getElementById('unsaved-indicator').style.visibility = 'visible';
            document.getElementById('btn-cancel').disabled = false;
            document.getElementById('btn-save').disabled = false;
        }
    }

    if (document.getElementById('btn-cancel')) {
        document.getElementById('btn-cancel').addEventListener('click', function() {
            window.location.reload();
        });
    }

    function updateBadge(cb) {
        const container = cb.closest('[data-hierarchy-level]');
        if (!container) return;
        const badgeContainer = container.querySelector('.access-badge-container');
        if (!badgeContainer) return;
        
        if (cb.disabled) {
            badgeContainer.innerHTML = BADGE_INHERITED;
        } else if (cb.checked) {
            badgeContainer.innerHTML = BADGE_ALLOWED;
        } else {
            badgeContainer.innerHTML = BADGE_RESTRICTED;
        }
    }

    function updateChildren(parentCb) {
        const parentLevel = parentCb.dataset.type; 
        let containerClass = '';
        if (parentLevel === 'subject') containerClass = '.subject-container';
        else if (parentLevel === 'topic') containerClass = '.topic-container';
        
        if (!containerClass) return; 
        
        const container = parentCb.closest(containerClass);
        if (!container) return;

        const childChecks = container.querySelectorAll('.access-checkbox');
        
        childChecks.forEach(cb => {
            if (cb === parentCb) return;
            
            if (!parentCb.checked) { 
                cb.disabled = true;
                cb.checked = false; 
                cb.dataset.inherited = 'true';
            } else { 
                if (cb.dataset.inherited === 'true') {
                    cb.disabled = false;
                    const explicit = cb.dataset.explicit === 'true';
                    cb.checked = !explicit;
                    delete cb.dataset.inherited;
                }
            }
            updateBadge(cb);
        });
    }

    function initCounts() {
        let allowed = { subject: 0, topic: 0, video: 0, resource: 0 };
        let restricted = { subject: 0, topic: 0, video: 0, resource: 0 };
        
        checkboxes.forEach(cb => {
            const type = cb.dataset.type;
            if (cb.checked === true) {
                if (allowed[type] !== undefined) allowed[type]++;
            } else if (cb.checked === false && cb.disabled === false) {
                if (restricted[type] !== undefined) restricted[type]++;
            }
            // Inherited items (cb.disabled === true) are not counted in either summary.
        });
        
        const allowedSummary = document.getElementById('allowed-summary');
        if (allowedSummary) {
            allowedSummary.innerHTML = `${allowed.subject} Subjects &bull; ${allowed.topic} Topics &bull; ${allowed.video} Videos &bull; ${allowed.resource} Materials`;
        }
        
        const restrictedSummary = document.getElementById('restricted-summary');
        if (restrictedSummary) {
            restrictedSummary.innerHTML = `${restricted.subject} Subjects &bull; ${restricted.topic} Topics &bull; ${restricted.video} Videos &bull; ${restricted.resource} Materials`;
        }
    }

    checkboxes.forEach(cb => {
        updateBadge(cb);
        cb.addEventListener('change', function () {
            if (!cb.disabled) {
                cb.dataset.explicit = (!cb.checked).toString();
            }
            updateBadge(cb);
            updateChildren(cb);
            setDirty();
            initCounts();
        });
    });

    initCounts();

    function applySearchAndFilter() {
        if (!searchInput) return;
        const query = searchInput.value.toLowerCase().trim();
        const filterState = document.querySelector('input[name="accessFilter"]:checked').value;
        
        const subjects = document.querySelectorAll('.subject-container');
        
        subjects.forEach(subjectEl => {
            let subjectVisible = false;
            const subjectText = subjectEl.dataset.searchText;
            const subjectCb = subjectEl.querySelector('.access-checkbox[data-type="subject"]');
            const subjectMatchesFilter = matchesFilter(subjectCb, filterState);
            const subjectMatchesSearch = query === '' || subjectText.includes(query);
            
            const topics = subjectEl.querySelectorAll('.topic-container');
            
            topics.forEach(topicEl => {
                let topicVisible = false;
                const topicText = topicEl.dataset.searchText;
                const topicCb = topicEl.querySelector('.access-checkbox[data-type="topic"]');
                const topicMatchesFilter = matchesFilter(topicCb, filterState);
                const topicMatchesSearch = query === '' || topicText.includes(query);
                
                const items = topicEl.querySelectorAll('.content-container');
                
                items.forEach(itemEl => {
                    const itemText = itemEl.dataset.searchText;
                    const itemCb = itemEl.querySelector('.access-checkbox[data-type="video"], .access-checkbox[data-type="resource"]');
                    const itemMatchesFilter = matchesFilter(itemCb, filterState);
                    const itemMatchesSearch = query === '' || itemText.includes(query);
                    
                    const itemVisible = itemMatchesFilter && (itemMatchesSearch || (query !== '' && (topicMatchesSearch || subjectMatchesSearch)));
                    itemEl.style.display = itemVisible ? '' : 'none';
                    
                    if (itemVisible) {
                        topicVisible = true;
                    }
                });
                
                const selfVisible = topicMatchesFilter && (topicMatchesSearch || subjectMatchesSearch);
                topicVisible = topicVisible || selfVisible;
                topicEl.style.display = topicVisible ? '' : 'none';
                
                if (topicVisible) {
                    subjectVisible = true;
                }
            });
            
            const selfVisible = subjectMatchesFilter && subjectMatchesSearch;
            subjectVisible = subjectVisible || selfVisible;
            subjectEl.style.display = subjectVisible ? '' : 'none';
        });
    }

    function matchesFilter(cb, filterState) {
        if (!cb) return false;
        if (filterState === 'all') return true;
        if (filterState === 'allowed') {
            return cb.checked && !cb.disabled;
        }
        if (filterState === 'restricted') {
            return !cb.checked || cb.disabled;
        }
        return true;
    }

    if(searchInput) searchInput.addEventListener('input', applySearchAndFilter);
    filterRadios.forEach(radio => radio.addEventListener('change', applySearchAndFilter));

    if (btnExpandAll) {
        btnExpandAll.addEventListener('click', function() {
            document.querySelectorAll('.collapse').forEach(collapseEl => {
                if(typeof bootstrap !== 'undefined') {
                    const bsCollapse = bootstrap.Collapse.getInstance(collapseEl) || new bootstrap.Collapse(collapseEl, {toggle: false});
                    bsCollapse.show();
                } else {
                    collapseEl.classList.add('show');
                }
            });
            document.querySelectorAll('[data-bs-toggle="collapse"]').forEach(btn => btn.setAttribute('aria-expanded', 'true'));
        });
    }

    if (btnCollapseAll) {
        btnCollapseAll.addEventListener('click', function() {
            document.querySelectorAll('.collapse').forEach(collapseEl => {
                if(typeof bootstrap !== 'undefined') {
                    const bsCollapse = bootstrap.Collapse.getInstance(collapseEl) || new bootstrap.Collapse(collapseEl, {toggle: false});
                    bsCollapse.hide();
                } else {
                    collapseEl.classList.remove('show');
                }
            });
            document.querySelectorAll('[data-bs-toggle="collapse"]').forEach(btn => btn.setAttribute('aria-expanded', 'false'));
        });
    }

    if (form) {
        form.addEventListener('submit', function (e) {
            const denied = [];
            checkboxes.forEach(cb => {
                if ((!cb.checked && !cb.disabled) || cb.dataset.explicit === 'true') {
                    denied.push({ type: cb.dataset.type, id: parseInt(cb.dataset.id, 10) });
                }
            });
            if (hiddenInput) {
                hiddenInput.value = JSON.stringify(denied);
            }
        });
    }
});
