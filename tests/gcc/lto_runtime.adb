with Ada.Numerics.Long_Elementary_Functions;

procedure Lto_Runtime is
   --  Keep this procedure visible in the optimized dump, while allowing the
   --  elementary functions called by it to be inlined by LTO.
   pragma No_Inline (Lto_Runtime);

   package Math renames Ada.Numerics.Long_Elementary_Functions;
   Input  : Long_Float := 4.0 with Volatile;
   Result : Long_Float with Volatile;
begin
   --  Volatile input prevents constant folding from hiding a runtime call.
   Result := Math."**" (Input, 0.5);
   if Result /= 2.0 then
      raise Program_Error;
   end if;
end Lto_Runtime;
