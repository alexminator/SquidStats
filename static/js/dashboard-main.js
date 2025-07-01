// Variables globales para el estado actual de la vista.
let currentPage = 1;
let currentSearch = "";
const itemsPerPage = 15;


document.addEventListener("DOMContentLoaded", function() {
  // Referencias a elementos del DOM
  const searchInput = document.getElementById("username-search");
  const usersContainer = document.getElementById("users-container");
  const prevBtn = document.getElementById("prev-page");
  const nextBtn = document.getElementById("next-page");
  const firstBtn = document.getElementById("first-page");
  const lastBtn = document.getElementById("last-page");
  const pageNumbers = document.getElementById("page-numbers");
  const dateFilter = document.getElementById("date-filter");
  const clearSearchBtn = document.getElementById("clear-search");

  /**
   * Pide datos al servidor (paginados y filtrados) y actualiza la vista.
   * @param {number} page - El número de página a solicitar.
   * @param {string} search - El término de búsqueda a aplicar.
   */
  async function fetchAndRenderUsers(page = 1, search = "") {
    const selectedDate = dateFilter.value;
    if (!selectedDate) return;

    // Muestra un spinner de carga mientras se obtienen los datos.
    usersContainer.innerHTML = `<div class="loading-spinner flex justify-center items-center h-64 w-full col-span-full"><svg class="animate-spin h-12 w-12 text-blue-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg></div>`;
    
    try {
      const response = await fetch("/get-logs-by-date", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date: selectedDate, page, per_page: itemsPerPage, search })
      });

      if (!response.ok) throw new Error("Error al obtener los datos del servidor");
      
      const data = await response.json(); // Objeto con {users, total, page, total_pages, ...}
      
      renderUserCards(data.users);
      updatePaginationControls(data);

      currentPage = data.page;
      currentSearch = search;
      clearSearchBtn.style.display = search ? 'flex' : 'none';

    } catch (error) {
      console.error("Error:", error);
      usersContainer.innerHTML = '<div class="text-red-500 text-center p-4 col-span-full">Error al cargar los datos. Verifique la conexión con el servidor.</div>';
    }
  }

  /**
   * Renderiza las tarjetas de usuario en el contenedor principal.
   * @param {Array} usersData - Array de usuarios para la página actual.
   */
  function renderUserCards(usersData) {
    window.allUsersData = usersData; // Asigna a la variable global para que el modal la vea

    if (!usersData || usersData.length === 0) {
      usersContainer.innerHTML = `
        <div class="no-results-msg col-span-full text-center py-12">
          <i class="fas fa-user-slash text-4xl text-gray-400 mb-4"></i>
          <h3 class="text-xl font-semibold text-gray-600">No se encontraron usuarios</h3>
          <p class="text-gray-500 mt-2">Intenta con otro término de búsqueda o cambia la fecha.</p>
        </div>`;
      return;
    }

    usersContainer.innerHTML = usersData.map((user, index) => `
      <div class="user-card group bg-[#f0f2f5] text-center overflow-hidden relative rounded-lg shadow-md w-full max-w-[320px] mx-auto pt-[25px] pb-[70px] transition-all duration-300 ease-out hover:-translate-y-2 hover:shadow-xl hover:z-10" data-username="${user.username.toLowerCase()}">
        <div class="avatar-wrapper relative inline-block h-[100px] w-[100px] mb-[15px] relative z-[1]">
          <div class="avatar-effect absolute w-full h-0 bottom-[135%] left-0 rounded-full bg-[#1369ce] opacity-90 scale-[3] transition-all duration-300 ease-linear z-0 group-hover:h-full"></div>
          <div class="avatar-background absolute inset-0 rounded-full bg-[#1369ce] z-[1]"></div>
          <div class="avatar w-full h-full rounded-full bg-slate-200 flex items-center justify-center text-[2.5rem] text-slate-500 relative transition-all duration-900 ease-in-out group-hover:shadow-[0_0_0_10px_#f7f5ec] group-hover:scale-[0.7] z-[2]">
            <i class="fas fa-user"></i>
          </div>
        </div>
        <div class="user-info mt-[-15px] mb-4 px-[15px]">
          <h3 class="username font-semibold text-[1.2rem] text-[#1369ce] mb-0">${user.username}</h3>
          <h4 class="ip-address text-[0.9rem] text-gray-500">${user.ip}</h4>
        </div>
        <div class="card-action px-[15px] mt-[2px] relative z-[2]">
          <button class="activity-button w-full bg-[#1369ce] text-white font-bold py-2 rounded-md cursor-pointer transition-all duration-300 text-sm hover:bg-[#0d5bb5] hover:-translate-y-0.5 shadow-md" 
                  onclick="window.openLogsModal(${index});">
            ACTIVIDAD
          </button>
        </div>
        <ul class="card-footer absolute bottom-[-80px] left-0 w-full px-4 py-3 bg-[#1369ce] text-white text-sm flex justify-between transition-all duration-500 ease-in-out group-hover:bottom-0 shadow-[0_-4px_6px_rgba(0,0,0,0.2)] z-[1]">
          <li class="inline-block flex flex-col items-center">
            <span class="label text-xs font-light uppercase tracking-wide">Solicitudes:</span>
            <span class="value font-semib">${user.total_requests}</span>
          </li>
          <li class="inline-block flex flex-col items-center">
            <span class="label text-xs font-light uppercase tracking-wide">Datos:</span>
            <span class="value font-semib">${formatBytes(user.total_data)}</span>
          </li>
        </ul>
      </div>
    `).join('');
  }

  /**
   * Actualiza los botones y números de la paginación según la respuesta del servidor.
   * @param {object} data - El objeto de paginación del servidor {page, total_pages}.
   */
  function updatePaginationControls(data) {
    const { page, total_pages } = data;
    pageNumbers.innerHTML = "";
    
    // Almacena el total de páginas para el botón "last"
    pageNumbers.dataset.totalPages = total_pages;

    if (total_pages > 0) {
        const startPage = Math.max(1, page - 2);
        const endPage = Math.min(total_pages, page + 2);

        for (let i = startPage; i <= endPage; i++) {
            const btn = document.createElement("button");
            btn.textContent = i;
            btn.className = `pg-btn text-lg px-3 py-1 rounded-full shadow-md font-medium ${ i === page ? "bg-blue-500 text-white" : "bg-white text-gray-800 hover:bg-gray-300"}`;
            btn.onclick = () => fetchAndRenderUsers(i, currentSearch);
            pageNumbers.appendChild(btn);
        }
    }
    
    firstBtn.disabled = page === 1;
    prevBtn.disabled = page === 1;
    nextBtn.disabled = page === total_pages;
    lastBtn.disabled = page === total_pages;
  }
  
  /**
   * Función Debounce para retrasar la ejecución de una función (usado en la búsqueda).
   */
  function debounce(func, delay) {
    let timeout;
    return function(...args) {
      const context = this;
      clearTimeout(timeout);
      timeout = setTimeout(() => func.apply(context, args), delay);
    };
  }

  // --- Event Listeners Reconfigurados ---
  clearSearchBtn.addEventListener("click", () => { 
    searchInput.value = ""; 
    fetchAndRenderUsers(1, ""); 
  });
  
  searchInput.addEventListener("input", debounce(() => {
    fetchAndRenderUsers(1, searchInput.value);
  }, 400));

  prevBtn.addEventListener("click", () => {
    if (currentPage > 1) fetchAndRenderUsers(currentPage - 1, currentSearch);
  });

  nextBtn.addEventListener("click", () => {
    const totalPages = parseInt(pageNumbers.dataset.totalPages || '1');
    if (currentPage < totalPages) fetchAndRenderUsers(currentPage + 1, currentSearch);
  });
  
  firstBtn.addEventListener("click", () => fetchAndRenderUsers(1, currentSearch));
  
  lastBtn.addEventListener("click", () => {
    const totalPages = parseInt(pageNumbers.dataset.totalPages || '1');
    fetchAndRenderUsers(totalPages, currentSearch);
  });

  const today = new Date().toISOString().split('T')[0];
  if (!dateFilter.value) {
    dateFilter.value = today;
  }
  
  dateFilter.addEventListener("change", () => {
    searchInput.value = ""; // Resetea la búsqueda al cambiar de fecha
    fetchAndRenderUsers(1, ""); // Pide la primera página de la nueva fecha
  });
  
  // Carga inicial de datos al cargar la página.
  fetchAndRenderUsers(1, "");

  // Llama a la inicialización del modal que ahora está en modal-handler.js
  if (typeof initializeModalSearchBar === 'function') {
    initializeModalSearchBar();
  }
});