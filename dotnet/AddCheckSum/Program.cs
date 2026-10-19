using System;

namespace AddCheckSum
{
    internal class Program
    {
        static int Main(string[] args)
        {
            if (args.Length < 1)
            {
                Console.Error.WriteLine("Usage: AddCheckSum.exe <filename>");
                return 1;
            }

            try
            {
                var securityType = Type.GetTypeFromProgID("HxSecurityCom.HxSecurityCom");
                if (securityType == null)
                {
                    Console.Error.WriteLine(
                        "Hamilton HxSecurityCom is not registered. Ensure Hamilton VENUS is installed " +
                        "and its 32-bit COM components are registered.");
                    return 2;
                }

                dynamic security = Activator.CreateInstance(securityType);
                security.SetFileValidation(args[0], 0, "//");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("Checksum update failed: " + ex.Message);
                return 3;
            }
        }
    }
}
